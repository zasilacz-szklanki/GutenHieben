import base64
import json
import os
from azure.storage.blob import BlobServiceClient
from flask import (Flask, redirect, render_template, request,
                   send_from_directory, url_for, Response)

app = Flask(__name__)
app.secret_key = "twoj-sekret-klucz" 

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

        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(file, overwrite=True)

        print(f"Plik {file.filename} przesłany do Azure Blob Storage")
        flash(f"✅ Plik {file.filename} został przesłany pomyślnie!", "success")
        return redirect(url_for('files'))
    except Exception as e:
        print(f"Błąd przesyłania: {e}")
        flash("❌ Wystąpił błąd podczas przesyłania pliku.", "danger")
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

        blob_list = container_client.list_blobs(name_starts_with=prefix)

        files = []
        for blob in blob_list:
            files.append({
                "name": blob.name[len(prefix):],
                "last_modified": blob.last_modified
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
