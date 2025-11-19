import base64
import json
import os
from azure.storage.blob import BlobServiceClient
from flask import (Flask, redirect, render_template, request,
                   send_from_directory, url_for, Response)

app = Flask(__name__)

def get_user_id():
    return request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', 'anonymous')

def get_user_info():
    principal_header = request.headers.get('X-MS-CLIENT-PRINCIPAL')
    if not principal_header:
        return {"name": "anonymous", "provider": None}

    decoded = base64.b64decode(principal_header)
    principal_data = json.loads(decoded)

    print(principal_data)

    return {
        "name": principal_data.get("name"),
        "email": principal_data.get("userDetails"),
        "provider": principal_data.get("identityProvider")
    }

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

@app.route('/upload', methods=['POST'])
def upload():
    # Dane do połączenia z Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    if 'file' not in request.files:
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('index'))

    user_id = get_user_id()
    blob_name = f"{user_id}/{file.filename}"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        # Utwórz blob i prześlij plik
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(file, overwrite=True)

        print(f"Plik {file.filename} przesłany do Azure Blob Storage")
        return f"Plik {file.filename} został przesłany pomyślnie!"
    except Exception as e:
        print(f"Błąd przesyłania: {e}")
        return "Wystąpił błąd podczas przesyłania pliku."

@app.route('/files')
def files():
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    CONTAINER_NAME = "files"

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        user_id = get_user_id()
        prefix = f"{user_id}/"

        # Pobierz listę blobów
        blob_list = container_client.list_blobs(name_starts_with=prefix)

        files = []
        for blob in blob_list:
            files.append({
                "name": blob.name[len(prefix):],       # nazwa bez prefiksu
                "last_modified": blob.last_modified    # czas przesłania
            })

        print("Lista plików:", files)
        return render_template('files.html', files=files)
    except Exception as e:
        print(f"Błąd pobierania listy plików: {e}")
        return "Wystąpił błąd podczas pobierania listy plików."


@app.route('/download/<filename>')
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

        # Zwróć plik jako odpowiedź HTTP
        return Response(
            stream.readall(),
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )
    except Exception as e:
        print(f"Błąd pobierania pliku: {e}")
        return "Wystąpił błąd podczas pobierania pliku."

if __name__ == '__main__':
   app.run()
