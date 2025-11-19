import os
from azure.storage.blob import BlobServiceClient
from flask import (Flask, redirect, render_template, request,
                   send_from_directory, url_for)

app = Flask(__name__)

def get_user_id():
    return request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'anonymous')

@app.route('/')
def index():
   print('Request for index page received')
   return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/hello', methods=['POST'])
def hello():
   name = request.form.get('name')

   if name:
       print('Request for hello page received with name=%s' % name)
       return render_template('hello.html', name = name)
   else:
       print('Request for hello page received with no name or blank name -- redirecting')
       return redirect(url_for('index'))

@app.route('/test', methods=['GET'])
def test():
    return "To jest test metody GET"

@app.route('/logout')
def logout():
    return redirect("https://gutenhieben-b5b0a0hxfqgnczdh.polandcentral-01.azurewebsites.net/.auth/logout")

@app.route('/upload', methods=['POST'])
def upload():
    # Dane do połączenia z Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)

    if 'file' not in request.files:
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('index'))

    user_id = get_user_id()
    blob_name = f"{user_id}/{file.filename}"

    try:
        # Utwórz blob i prześlij plik
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(file, overwrite=True)

        print(f"Plik {file.filename} przesłany do Azure Blob Storage")
        return f"Plik {file.filename} został przesłany pomyślnie!"
    except Exception as e:
        print(f"Błąd przesyłania: {e}")
        return "Wystąpił błąd podczas przesyłania pliku."

@app.route('/files')
def list_files():
    # TODO wyświetlenie plików uzytkownika
    # Dane do połączenia z Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)

    try:
        # Pobierz listę blobów
        blob_list = container_client.list_blobs()
        files = [blob.name for blob in blob_list]

        print("Lista plików:", files)
        return render_template('files.html', files=files)
    except Exception as e:
        print(f"Błąd pobierania listy plików: {e}")
        return "Wystąpił błąd podczas pobierania listy plików."

if __name__ == '__main__':
   app.run()
