import base64
import json
import os
import zipfile
import groq
import threading
import io
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from flask import (Flask, redirect, render_template, request,
                   send_from_directory, url_for, Response, flash)


app = Flask(__name__)
# LOG_FILE removed, using Azure Blob instead
app.secret_key = "DTS" 

def get_user_id():
    return request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'anonymous')

def get_user_info():
    principal_header = request.headers.get('X-MS-CLIENT-PRINCIPAL')
    if not principal_header:
        return {"name": "anonymous", "email": None, "provider": None}

    try:
        decoded = base64.b64decode(principal_header)
        principal = json.loads(decoded)
    except Exception:
        return {"name": "anonymous", "email": None, "provider": None}

    claims = principal.get("claims", [])
    claim_map = {c.get("typ"): c.get("val") for c in claims if "typ" in c and "val" in c}

    name = (
        claim_map.get("name") or
        (
            (claim_map.get("given_name") and claim_map.get("family_name")) and
            f"{claim_map.get('given_name')} {claim_map.get('family_name')}"
        ) or
        claim_map.get("preferred_username") or
        claim_map.get("nickname") or
        principal.get("name") or
        principal.get("userDetails") or
        "anonymous"
    )

    email = (
        claim_map.get("email") or
        claim_map.get("emails") or
        principal.get("userDetails")
    )

    provider = principal.get("identityProvider")

    return {"name": name, "email": email, "provider": provider}

def add_to_logbook(action, filename, details="", user_id=None):
    """Dodaje wpis do logbooka użytkownika w Azure Blob Storage."""
    if not user_id:
        try:
            user_id = get_user_id()
        except:
             print("Brak user_id do logowania zdarzenia")
             return

    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"
    # Zmieniona ścieżka: logs/{user_id}.json
    blob_name = f"logs/{user_id}.json"
    
    logs = []
    
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        blob_client = container_client.get_blob_client(blob_name)
        
        if blob_client.exists():
            download_stream = blob_client.download_blob()
            data = download_stream.readall()
            if data:
                logs = json.loads(data)
    except Exception as e:
        print(f"Błąd odczytu logbooka: {e}")
        logs = []

    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "filename": filename,
        "details": details
    }
    
    logs.insert(0, entry)
    
    try:
        if 'blob_client' not in locals():
             blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
             container_client = blob_service_client.get_container_client(CONTAINER_NAME)
             blob_client = container_client.get_blob_client(blob_name)

        blob_client.upload_blob(json.dumps(logs, indent=4, ensure_ascii=False), overwrite=True)
    except Exception as e:
        print(f"Błąd zapisu logbooka: {e}")

def get_logbook_entries():
    """Pobiera wpisy z logbooka użytkownika."""
    try:
        user_id = get_user_id()
        AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        CONTAINER_NAME = "files"
        # Zmieniona ścieżka: logs/{user_id}.json
        blob_name = f"logs/{user_id}.json"
        
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        blob_client = container_client.get_blob_client(blob_name)
        
        if blob_client.exists():
            download_stream = blob_client.download_blob()
            return json.loads(download_stream.readall())
    except Exception as e:
        print(f"Błąd pobierania logbooka: {e}")
    
    return []

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

@app.route('/logout')
def logout():
    return redirect("https://gutenhieben-b5b0a0hxfqgnczdh.polandcentral-01.azurewebsites.net/.auth/logout")

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
                    safe_filename = inner_filename.lstrip('/')
                    target_blob_name = f"{user_id}/{safe_filename}"
                    target_blob_client = container_client.get_blob_client(target_blob_name)
                    
                    exists = target_blob_client.exists()
                    
                    if exists:
                        filename_base, filename_ext = os.path.splitext(safe_filename)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        new_filename = f"{filename_base}_{timestamp}{filename_ext}"
                        
                        target_blob_name = f"{user_id}/{new_filename}"
                        target_blob_client = container_client.get_blob_client(target_blob_name)
                        
                        add_to_logbook("Wersjonowanie tło", safe_filename, f"Plik istniał. Zmieniono nazwę na: {new_filename}", user_id=user_id)

                    with z.open(inner_filename) as f:
                        target_blob_client.upload_blob(f, overwrite=True)
                    count += 1
                    
        add_to_logbook("Rozpakowanie", filename, f"Rozpakowano archiwum ZIP ({count} plików)", user_id=user_id)
        
        blob_client.delete_blob()
        add_to_logbook("Usunięcie ZIP", filename, "Usunięto plik ZIP po rozpakowaniu (zostawiono samą jego zawartość)", user_id=user_id)

    except Exception as e:
        print(f"Background unpack error for {filename}: {e}")
        add_to_logbook("Błąd Rozpakowania", filename, str(e), user_id=user_id)

@app.route('/upload', methods=['POST'])
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
            
            add_to_logbook("Wersjonowanie", original_filename, f"Plik istniał. Zmieniono nazwę na: {new_filename}", user_id=user_id)
            
            file.filename = new_filename

        blob_client.upload_blob(file, overwrite=True)

        add_to_logbook("Upload", file.filename, "Pomyślnie wgrano plik do chmury", user_id=user_id)

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
        add_to_logbook("Błąd Uploadu", original_filename, str(e), user_id=user_id)
        flash("Wystąpił błąd podczas przesyłania pliku.", "danger")
        return redirect(url_for('index'))

@app.route('/files')
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
        return f"Wystąpił błąd podczas pobierania listy plików. {e}"

@app.route('/download/<path:filename>')
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
        add_to_logbook("Usunięcie", filename, "Usunięto plik z chmury", user_id=user_id)
        
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
        deleted_files = ""

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
                deleted_files += f"{filename}\n"
                success_count += 1
            except Exception as e:
                print(f"Błąd usuwania pliku {filename}: {e}")
                fail_count += 1

        if success_count > 0:
            flash(f"Pomyślnie usunięto {success_count} plików.", "success")
            add_to_logbook(f"Pomyślnie usunięto {success_count} plików. Usunięte pliki: {deleted_files}", "success", user_id=user_id)
        if fail_count > 0:
            flash(f"Nie udało się usunąć {fail_count} plików.", "danger")

    except Exception as e:
        print(f"Błąd połączenia z Azure: {e}")
        flash("Wystąpił błąd podczas usuwania plików.", "danger")
    
    return redirect(url_for('files'))

@app.route('/describe', methods=['POST'])
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
        
        add_to_logbook("AI Opis", filename, "Wygenerowano opis przy użyciu Groq/Llama", user_id=user_id)
        # Save description to subfolder
        try:
            desc_blob_name = f"{user_id}/descriptions/{filename}.txt"
            desc_blob_client = container_client.get_blob_client(desc_blob_name)
            desc_blob_client.upload_blob(description, overwrite=True)
            print(f"Zapisano opis dla {filename}")
        except Exception as upload_e:
            print(f"Błąd zapisu opisu: {upload_e}")
            # Non-critical error, continue to show it to user
        
        # We need to pass this description back to the template.
        # Since we redirect, we use flash, but distinct category or special handling in template?
        # Let's use a special flash category 'description'.
        flash(description, "description_result") # Use specific category for modal popup in UI
        
    except Exception as e:
        print(f"Błąd generowania opisu: {e}")
        flash(f"Wystąpił błąd podczas generowania opisu: {e}", "danger")

    return redirect(url_for('files'))

@app.route('/view_description', methods=['POST'])
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
def logbook_view():
    entries = get_logbook_entries()
    return render_template('logbook.html', entries=entries)

if __name__ == '__main__':
   app.run()
