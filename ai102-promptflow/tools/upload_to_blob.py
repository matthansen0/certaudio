
import os

def entry(file_path: str, container_name: str, blob_name: str) -> str:
    use_msi = os.getenv("AZURE_USE_MSI", "false").lower() in ("1","true","yes")
    from azure.storage.blob import BlobServiceClient

    if use_msi:
        from azure.identity import DefaultAzureCredential
        account_url = os.getenv("BLOB_ACCOUNT_URL")
        if not account_url:
            raise RuntimeError("BLOB_ACCOUNT_URL must be set when using MSI.")
        cred = DefaultAzureCredential()
        bsc = BlobServiceClient(account_url=account_url, credential=cred)
    else:
        conn_str = os.getenv("STORAGE_CONNECTION_STRING")
        if not conn_str:
            raise RuntimeError("STORAGE_CONNECTION_STRING not set.")
        bsc = BlobServiceClient.from_connection_string(conn_str)

    container = bsc.get_container_client(container_name)
    try:
        container.create_container()
    except Exception:
        pass

    with open(file_path, "rb") as f:
        container.upload_blob(name=blob_name, data=f, overwrite=True, content_type=_guess_mime(file_path))

    return container.get_blob_client(blob_name).url

def _guess_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".md":  "text/markdown",
        ".txt": "text/plain",
        ".ssml":"application/ssml+xml",
        ".xml": "application/xml",
    }.get(ext, "application/octet-stream")
