import os
import getpass
import paramiko
import stat
from concurrent.futures import ThreadPoolExecutor

REMOTE_DIRS = [
    "/opt/jibo/Photos/cache",
    "/opt/jibo/Photos/upload",
]
LOCAL_BASE = "Photos"


def connect_sftp(ip, username="root", password="jibo"):
    """Try to open an SFTP connection; return (sftp, transport) or (None, None) on failure."""
    try:
        transport = paramiko.Transport((ip, 22))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        return sftp, transport
    except Exception as e:
        print(f"Connection failed for {username}@{ip}: {e}")
        return None, None


def _download_files_chunk(ip, username, password, files):
    """Worker that downloads a chunk of files using its own SFTP connection."""
    sftp, transport = connect_sftp(ip, username, password)
    if sftp is None:
        print(f"Worker could not connect for {username}@{ip}")
        return
    try:
        for remote_path, local_path in files:
            try:
                print(f"Downloading {remote_path} -> {local_path}")
                sftp.get(remote_path, local_path)
            except Exception as e:
                print(f"Failed to download {remote_path}: {e}")
    finally:
        if sftp is not None:
            sftp.close()
        if transport is not None:
            transport.close()


def download_directory_parallel(ip, username, password, remote_dir, local_dir, max_workers=4):
    """
    Download files from a single-level directory using multiple parallel connections.
    Skips subdirectories and files that already exist locally with the same size.
    """
    try:
        os.makedirs(local_dir, exist_ok=True)

        # Use a single control connection to list directory contents
        sftp, transport = connect_sftp(ip, username, password)
        if sftp is None:
            print(f"Could not list directory {remote_dir}")
            return

        try:
            entries = sftp.listdir_attr(remote_dir)
        finally:
            if sftp is not None:
                sftp.close()
            if transport is not None:
                transport.close()

        files_to_download = []
        for entry in entries:
            # Skip if it's a directory
            if stat.S_ISDIR(entry.st_mode):
                continue

            remote_path = f"{remote_dir.rstrip('/')}/{entry.filename}"
            local_path = os.path.join(local_dir, entry.filename)

            # Skip if file exists and size matches
            if os.path.exists(local_path):
                try:
                    if os.path.getsize(local_path) == entry.st_size:
                        continue
                except OSError:
                    pass

            files_to_download.append((remote_path, local_path))

        if not files_to_download:
            print(f"No new files to download from {remote_dir}")
            return

        # Limit workers to number of files so we do not spawn idle threads
        workers = min(max_workers, len(files_to_download))

        # Split files into roughly equal chunks
        chunks = [[] for _ in range(workers)]
        for idx, file_info in enumerate(files_to_download):
            chunks[idx % workers].append(file_info)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            for chunk in chunks:
                if not chunk:
                    continue
                executor.submit(_download_files_chunk, ip, username, password, chunk)

    except FileNotFoundError:
        print(f"Remote directory not found: {remote_dir}")
    except Exception as e:
        print(f"Error downloading from {remote_dir}: {e}")


def main():
    ip = input("Enter Jibo IP address: ").strip()

    # First attempt with default root/jibo
    username = "root"
    password = "jibo"
    print(f"Trying default credentials {username}/{password}...")
    test_sftp, test_transport = connect_sftp(ip, username, password)

    # If that fails, ask user for credentials
    if test_sftp is None:
        print("Default credentials failed. Please enter credentials manually.")
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        test_sftp, test_transport = connect_sftp(ip, username, password)

        if test_sftp is None:
            print("Could not connect with provided credentials. Exiting.")
            return

    # Close the initial test connection; workers will open their own.
    if test_sftp is not None:
        test_sftp.close()
    if test_transport is not None:
        test_transport.close()

    # Download files in parallel
    for remote_dir in REMOTE_DIRS:
        # Map to Photos/cache and Photos/upload locally
        subdir_name = os.path.basename(remote_dir.rstrip("/")) or "root"
        local_dir = os.path.join(LOCAL_BASE, subdir_name)
        download_directory_parallel(ip, username, password, remote_dir, local_dir)

    print("Done.")


if __name__ == "__main__":
    # Set host key policy so we don't fail on unknown hosts
    paramiko.util.log_to_file("paramiko.log")  # optional: comment out if you don't want logs
    # Accept unknown host keys automatically
    paramiko.client.SSHClient().set_missing_host_key_policy(paramiko.AutoAddPolicy())
    main()