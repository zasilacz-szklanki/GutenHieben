import base64
import json
import os
import zipfile
import groq
import threading
import io
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from azure.data.tables import TableServiceClient, UpdateMode
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
LOG_FILE = 'logbook.json'
TABLE_NAME = 'users'
ADMIN_SECRET = 'Cloud2025'

app.secret_key = "DTS"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, is_admin=False):
        self.id = id
        self.is_admin = is_admin

def get_table_client():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    table_service = TableServiceClient.from_connection_string(conn_str=AZURE_STORAGE_CONNECTION_STRING)
    table_client = table_service.get_table_client(table_name=TABLE_NAME)
    try:
        table_client.create_table()
    except ResourceExistsError:
        pass
    return table_client

@login_manager.user_loader
def load_user(user_id):
    try:
        table_client = get_table_client()
        user_entity = table_client.get_entity(partition_key="users", row_key=user_id)
        return User(user_id, user_entity.get('is_admin', False))
    except ResourceNotFoundError:
        return None
    except Exception as e:
        print(f"Błąd ładowania użytkownika: {e}")
        return None

def authenticate_user(username, password):
    try:
        table_client = get_table_client()
        user_entity = table_client.get_entity(partition_key="users", row_key=username)
        if check_password_hash(user_entity['password'], password):
            return User(username, user_entity.get('is_admin', False))
    except ResourceNotFoundError:
        return None
    except Exception as e:
        print(f"Bład autentykacji: {e}")
    return None

def create_user(username, password, is_admin=False):
    table_client = get_table_client()
    user_entity = {
        'PartitionKey': "users",
        'RowKey': username,
        'password': generate_password_hash(password),
        'is_admin': is_admin
    }
    table_client.create_entity(entity=user_entity)

def add_to_logbook(action, filename, details=""):
    """Dodaje wpis do logbooka."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "filename": filename,
        "details": details
    }
    
    logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except (json.JSONDecodeError, ValueError):
            logs = []
    
    logs.insert(0, entry)
    
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

def get_logbook_entries():
    """Pobiera wpisy z logbooka."""
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def get_user_id():
    if current_user.is_authenticated:
        return current_user.id
    return 'anonymous'

def get_user_info():
    if current_user.is_authenticated:
        return {"name": current_user.id}
    return {"name": "anonymous"}

def background_unpack(user_id, filename):
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        prefix = f"{user_id}/"
        blob_name = prefix + filename
        
        blob_client = container_client.get_blob_client(blob_name)
        download_stream = blob_client.download_blob()
        
        file_content = io.BytesIO(download_stream.readall())
        
        count = 0
        with zipfile.ZipFile(file_content) as z:
            for inner_filename in z.namelist():
                if not inner_filename.endswith('/'):
                    target_blob_name = f"{user_id}/{inner_filename}"
                    target_blob_client = container_client.get_blob_client(target_blob_name)
                    
                    if target_blob_client.exists():
                        filename_base, filename_ext = os.path.splitext(inner_filename)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        new_filename = f"{filename_base}_{timestamp}{filename_ext}"
                        
                        target_blob_name = f"{user_id}/{new_filename}"
                        target_blob_client = container_client.get_blob_client(target_blob_name)
                        
                        add_to_logbook("Wersjonowanie tło", inner_filename, f"Plik istniał. Zmieniono nazwę na: {new_filename}")

                    with z.open(inner_filename) as f:
                        target_blob_client.upload_blob(f, overwrite=True)
                    count += 1
                    
        add_to_logbook("Rozpakowanie tło", filename, f"Pomyślnie rozpakowano {count} plików z tła")
        print(f"Background unpack finished for {filename}: {count} files.")
        
        blob_client.delete_blob()
        add_to_logbook("Usunięcie ZIP", filename, "Usunięto plik ZIP po rozpakowaniu (zostawiono samą jego zawartość)")

    except Exception as e:
        print(f"Background unpack error for {filename}: {e}")
        add_to_logbook("Błąd Rozpakowania tło", filename, str(e))

@app.route('/')
def index():
   print('Request for index page received')
   user_info = get_user_info()
   return render_template('index.html', name = user_info["name"])

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/test', methods=['GET'])
def test():
    return "To jest test metody GET"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = authenticate_user(username, password)
        
        if user:
            login_user(user)
            flash('Zalogowano pomyślnie!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Nieprawidłowa nazwa użytkownika lub hasło.', 'danger')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        is_admin = request.form.get('is_admin') == '1'
        admin_code = request.form.get('admin_code')
        
        try:
            table_client = get_table_client()
            table_client.get_entity(partition_key="users", row_key=username)
            flash('Użytkownik o takiej nazwie już istnieje.', 'warning')
            return redirect(url_for('register'))
        except ResourceNotFoundError:
            pass
        except Exception as e:
            flash(f"Błąd bazy danych: {e}", "danger")
            return redirect(url_for('register'))

            
        if is_admin and admin_code != ADMIN_SECRET:
             flash('Nieprawidłowy kod administratora.', 'danger')
             return redirect(url_for('register'))
        
        try:
            create_user(username, password, is_admin)
            flash('Konto utworzone pomyślnie! Możesz się zalogować.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
             flash(f'Błąd podczas tworzenia konta: {e}', 'danger')
             return redirect(url_for('register'))
        
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Wylogowano pomyślnie.', 'info')
    return redirect(url_for('index'))





@app.route('/upload', methods=['POST'])
@login_required
def upload():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    if 'file' not in request.files:
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('index'))

    user_id = get_user_id()
    original_filename = file.filename
    blob_name = f"{user_id}/{original_filename}"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        blob_client = container_client.get_blob_client(blob_name)

        if blob_client.exists():
            filename_base, filename_ext = os.path.splitext(original_filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_filename = f"{filename_base}_{timestamp}{filename_ext}"
            
            blob_name = f"{user_id}/{new_filename}"
            blob_client = container_client.get_blob_client(blob_name)
            
            add_to_logbook("Wersjonowanie", original_filename, f"Plik istniał. Zmieniono nazwę na: {new_filename}")
            
            file.filename = new_filename

        blob_client.upload_blob(file, overwrite=True)

        add_to_logbook("Upload", file.filename, "Pomyślnie wgrano plik do chmury")

        print(f"Plik {file.filename} przesłany do Azure Blob Storage")
        
        if file.filename.lower().endswith('.zip'):
            thread = threading.Thread(target=background_unpack, args=(user_id, file.filename))
            thread.start()
            flash(f"Plik {file.filename} przesłany. Rozpakowywanie w tle rozpoczęte.", "info")
        else:
            flash(f"Plik {file.filename} został przesłany pomyślnie!", "success")
            
        return redirect(url_for('files'))

    except Exception as e:
        print(f"Błąd przesyłania: {e}")
        add_to_logbook("Błąd Uploadu", original_filename, str(e))
        flash("Wystąpił błąd podczas przesyłania pliku.", "danger")
        return redirect(url_for('index'))



@app.route('/files')
@login_required
def files():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        user_id = get_user_id()
        prefix = f"{user_id}/"

        blob_list = list(container_client.list_blobs(name_starts_with=prefix))
        
        description_files = set()
        user_files = []

        for blob in blob_list:
            blob_relative_name = blob.name[len(prefix):]
            if blob_relative_name.startswith('descriptions/'):
                description_files.add(blob_relative_name[len('descriptions/'):])
            else:
                user_files.append(blob)

        files = []
        for blob in user_files:
            blob_relative_name = blob.name[len(prefix):]
            has_desc = f"{blob_relative_name}.txt" in description_files
            
            files.append({
                "name": blob_relative_name,
                "created_on": blob.creation_time,
                "last_modified": blob.last_modified,
                "size": blob.size,
                "has_description": has_desc
            })

        print("Lista plików:", files)
        return render_template('files.html', files=files)
    except Exception as e:
        print(f"Błąd pobierania listy plików: {e}")
        return "Wystąpił błąd podczas pobierania listy plików."


@app.route('/download/<path:filename>')
@login_required
def download(filename):
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        user_id = get_user_id()
        prefix = f"{user_id}/"
        blob_name = prefix + filename

        blob_client = container_client.get_blob_client(blob_name)
        stream = blob_client.download_blob()

        return Response(
            stream.readall(),
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )
    except Exception as e:
        print(f"Błąd pobierania pliku: {e}")
        return "Wystąpił błąd podczas pobierania pliku."

@app.route('/delete', methods=['POST'])
@login_required
def delete_file():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    filename = request.form.get('filename')
    if not filename:
        flash("Brak nazwy pliku do usunięcia.", "danger")
        return redirect(url_for('files'))

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)

        user_id = get_user_id()
        prefix = f"{user_id}/"
        blob_name = prefix + filename

        blob_client = container_client.get_blob_client(blob_name)
        blob_client.delete_blob()
        add_to_logbook("Usunięcie", filename, "Usunięto plik z chmury")
        
        try:
            desc_blob_name = f"{user_id}/descriptions/{filename}.txt"
            desc_blob_client = container_client.get_blob_client(desc_blob_name)
            desc_blob_client.delete_blob()
        except:
            pass

        flash(f"Plik {filename} został usunięty.", "success")
    except Exception as e:
        print(f"Błąd usuwania pliku: {e}")
        flash(f"Nie udało się usunąć pliku {filename}.", "danger")

    return redirect(url_for('files'))

@app.route('/delete_multiple', methods=['POST'])
@login_required
def delete_multiple():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    filenames = request.form.getlist('filenames')
    if not filenames:
        flash("Nie wybrano żadnych plików do usunięcia.", "warning")
        return redirect(url_for('files'))

    success_count = 0
    fail_count = 0

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        user_id = get_user_id()
        prefix = f"{user_id}/"

        for filename in filenames:
            try:
                blob_name = prefix + filename
                blob_client = container_client.get_blob_client(blob_name)
                blob_client.delete_blob()
                
                try:
                    desc_blob_name = f"{user_id}/descriptions/{filename}.txt"
                    desc_blob_client = container_client.get_blob_client(desc_blob_name)
                    desc_blob_client.delete_blob()
                except:
                    pass

                success_count += 1
            except Exception as e:
                print(f"Błąd usuwania pliku {filename}: {e}")
                fail_count += 1

        if success_count > 0:
            flash(f"Pomyślnie usunięto {success_count} plików.", "success")
        if fail_count > 0:
            flash(f"Nie udało się usunąć {fail_count} plików.", "danger")

    except Exception as e:
        print(f"Błąd połączenia z Azure: {e}")
        flash("Wystąpił błąd podczas usuwania plików.", "danger")
    
    return redirect(url_for('files'))

@app.route('/unpack', methods=['POST'])
@login_required
def unpack_file():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    filename = request.form.get('filename')
    if not filename:
        flash("Brak nazwy pliku do rozpakowania.", "danger")
        return redirect(url_for('files'))

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        user_id = get_user_id()
        prefix = f"{user_id}/"
        blob_name = prefix + filename
        
        blob_client = container_client.get_blob_client(blob_name)
        download_stream = blob_client.download_blob()
        
        import io
        file_content = io.BytesIO(download_stream.readall())
        
        with zipfile.ZipFile(file_content) as z:
            count = 0
            for inner_filename in z.namelist():
                if not inner_filename.endswith('/'):
                    target_blob_name = f"{user_id}/{inner_filename}"
                    target_blob_client = container_client.get_blob_client(target_blob_name)
                    with z.open(inner_filename) as f:
                        target_blob_client.upload_blob(f, overwrite=True)
                    count += 1

        add_to_logbook("Rozpakowanie", filename, f"Rozpakowano archiwum ZIP ({count} plików)")
        flash(f"Pomyślnie rozpakowano {count} plików z archiwum {filename}!", "success")

    except Exception as e:
        print(f"Błąd rozpakowywania: {e}")
        flash(f"Nie udało się rozpakować pliku: {e}", "danger")

    return redirect(url_for('files'))

@app.route('/describe', methods=['POST'])
@login_required
def describe_file():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")

    if not GROQ_API_KEY:
        flash("Brak klucza API GROQ. Skontaktuj się z administratorem.", "warning")
        return redirect(url_for('files'))

    filename = request.form.get('filename')
    if not filename:
        return redirect(url_for('files'))

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        user_id = get_user_id()
        blob_name = f"{user_id}/{filename}"
        blob_client = container_client.get_blob_client(blob_name)

        props = blob_client.get_blob_properties()
        if props.size > 5 * 1024 * 1024:
            flash(f"Plik {filename} jest zbyt duży (>5MB) do wygenerowania opisu.", "warning")
            return redirect(url_for('files'))

        download_stream = blob_client.download_blob()
        content_bytes = download_stream.readall()
        
        try:
            content_text = content_bytes.decode('utf-8')
        except UnicodeDecodeError:
             flash("Tylko pliki tekstowe są obsługiwane.", "warning")
             return redirect(url_for('files'))
        
        client = groq.Groq(api_key=GROQ_API_KEY)
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": f"Stwórz krótkie, jednozdaniowe podsumowanie zawartości tego pliku:\n\n{content_text[:20000]}", 
                }
            ],
            model="llama-3.3-70b-versatile",
        )
        
        description = chat_completion.choices[0].message.content
        
        add_to_logbook("AI Opis", filename, "Wygenerowano opis przy użyciu Groq/Llama")
        try:
            desc_blob_name = f"{user_id}/descriptions/{filename}.txt"
            desc_blob_client = container_client.get_blob_client(desc_blob_name)
            desc_blob_client.upload_blob(description, overwrite=True)
            print(f"Zapisano opis dla {filename}")
        except Exception as upload_e:
            print(f"Błąd zapisu opisu: {upload_e}")
            
        flash(description, "description_result")
        
    except Exception as e:
        print(f"Błąd generowania opisu: {e}")
        flash(f"Wystąpił błąd podczas generowania opisu: {e}", "danger")

    return redirect(url_for('files'))

@app.route('/view_description', methods=['POST'])
@login_required
def view_description():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    filename = request.form.get('filename')
    if not filename:
        return redirect(url_for('files'))
    
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        user_id = get_user_id()
        desc_blob_name = f"{user_id}/descriptions/{filename}.txt"
        
        blob_client = container_client.get_blob_client(desc_blob_name)
        stream = blob_client.download_blob()
        description = stream.readall().decode('utf-8')
        
        flash(description, "description_result")

    except Exception as e:
        print(f"Błąd pobierania opisu: {e}")
        flash(f"Nie udało się pobrać opisu: {e}", "danger")

    return redirect(url_for('files'))


@app.route('/logbook')
@login_required
def logbook_view():
    if not current_user.is_admin:
        flash("Brak uprawnień do przeglądania logbooka.", "danger")
        return redirect(url_for('index'))
    entries = get_logbook_entries()
    return render_template('logbook.html', entries=entries)

if __name__ == '__main__':
   app.run()
