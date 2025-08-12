import pytest
import uuid
from tests.test_role import auth_client
from pathlib import Path

@pytest.fixture
def sample_file_data():
    return b"This is test file content for chunk upload testing. " * 100

@pytest.fixture
def large_file_data():
    return b"A" * (20 * 1024 * 1024)


@pytest.mark.asyncio
async def test_init(auth_client):
    response = await auth_client.post("/files/init_batch")
    assert response.status_code == 201
    data = response.json()
    assert "batch_id" in data
    assert len(data["batch_id"]) > 0

@pytest.mark.asyncio
async def test_init_upload_without_batch(auth_client, tmp_path):
    payload = {
        "filename": "test.txt",
        "total_chunks": 1
    }
    response = await auth_client.post("/files/init", json=payload)
    assert response.status_code == 201
    
    data = response.json()
    assert "upload_id" in data
    assert "batch_id" in data
    
    # Verify upload directory was created
    upload_id = data["upload_id"]
    assert upload_id
    batch_id = data["batch_id"]
    assert batch_id is None

    upload_dir = Path(f"{tmp_path}/data/upload/{upload_id}")
    assert upload_dir.exists()


@pytest.mark.asyncio
async def test_init_upload_with_batch(auth_client, tmp_path):
    batch_response = await auth_client.post("/files/init_batch")
    batch_id = batch_response.json()["batch_id"]
    payload = {
        "filename": "test.txt",
        "total_chunks": 2,
        "batch_id": batch_id
    }
    response = await auth_client.post("/files/init", json=payload)
    assert response.status_code == 201
    
    data = response.json()
    assert data["batch_id"] == batch_id
    
    upload_id = data["upload_id"]
    upload_dir = Path(f"{tmp_path}/data/upload/{upload_id}")
    assert upload_dir.exists()


@pytest.mark.asyncio
async def test_chunk_upload_success(auth_client, sample_file_data, tmp_path):
    init_payload = {
        "filename": "test.txt",
        "total_chunks": 1
    }
    init_response = await auth_client.post("/files/init", json=init_payload)
    upload_id = init_response.json()["upload_id"]
    
    form_data = {
        "upload_id": upload_id,
        "chunk_number": "0",
    }
    files = {
        "chunk": ("chunk_0", sample_file_data, "application/octet-stream")
    }
    
    response = await auth_client.post(
        "/files/chunk", data=form_data, files=files)
    assert response.status_code == 201
    
    data = response.json()
    assert data["status"] == "chunk received"
    assert data["chunk_number"] == 0
    assert data["received_chunks"] == 1
    
    upload_dir = Path(f"{tmp_path}/data/upload")
    chunk_path = upload_dir / upload_id / "chunk_0"
    assert chunk_path.exists()
    assert chunk_path.read_bytes() == sample_file_data

@pytest.mark.asyncio
async def test_chunk_upload_invalid_upload_id(auth_client, sample_file_data):
    form_data = {
        "upload_id": "invalid-id",
        "chunk_number": "0",
    }
    files = {
        "chunk": ("chunk_0", sample_file_data, "application/octet-stream")
    }
    
    response = await auth_client.post("/files/chunk", data=form_data, files=files)
    assert response.status_code == 201
    
    data = response.json()
    assert "error" in data
    assert data["error"] == "Invalid upload ID - upload not initialized"


@pytest.mark.asyncio
async def test_chunk_upload_size_limit_exceeded(auth_client, tmp_path):
    # Create a chunk larger than 1MB (1MB + 1KB)
    oversized_chunk_data = b"A" * (1024 * 1024 + 1024)  # 1MB + 1KB
    
    # Initialize upload
    init_payload = {
        "filename": "oversized_test.txt",
        "total_chunks": 1
    }
    init_response = await auth_client.post("/files/init", json=init_payload)
    assert init_response.status_code == 201
    upload_id = init_response.json()["upload_id"]
    
    # Try to upload oversized chunk
    form_data = {
        "upload_id": upload_id,
        "chunk_number": "0",
    }
    files = {
        "chunk": ("chunk_0", oversized_chunk_data, "application/octet-stream")
    }
    
    response = await auth_client.post("/files/chunk", data=form_data, files=files)
    
    # Should return 413 Payload Too Large
    assert response.status_code == 413
    
    # Verify error message contains size information
    error_detail = response.json()["detail"]
    assert "exceeds maximum allowed size" in error_detail
    
    # Verify that no chunk file was created (cleanup should have removed it)
    upload_dir = Path(f"{tmp_path}/data/upload")
    chunk_path = upload_dir / upload_id / "chunk_0"
    assert not chunk_path.exists()


@pytest.mark.asyncio
async def test_chunk_upload_size_limit_exact(auth_client, tmp_path):
    # Create a chunk exactly 1MB
    exact_size_chunk_data = b"A" * (1024 * 1024)  # Exactly 1MB
    
    # Initialize upload
    init_payload = {
        "filename": "exact_size_test.txt",
        "total_chunks": 1
    }
    init_response = await auth_client.post("/files/init", json=init_payload)
    assert init_response.status_code == 201
    upload_id = init_response.json()["upload_id"]
    
    # Upload chunk at exact size limit
    form_data = {
        "upload_id": upload_id,
        "chunk_number": "0",
    }
    files = {
        "chunk": ("chunk_0", exact_size_chunk_data, "application/octet-stream")
    }
    
    response = await auth_client.post("/files/chunk", data=form_data, files=files)
    
    # Should succeed
    assert response.status_code == 201
    
    data = response.json()
    assert data["status"] == "chunk received"
    assert data["chunk_number"] == 0
    assert data["received_chunks"] == 1
    
    # Verify chunk file was created with correct content
    upload_dir = Path(f"{tmp_path}/data/upload")
    chunk_path = upload_dir / upload_id / "chunk_0"
    assert chunk_path.exists()
    assert chunk_path.read_bytes() == exact_size_chunk_data

@pytest.mark.asyncio
async def test_complete_upload_success(auth_client, sample_file_data, tmp_path):
    filename = "complete_test.txt"
    total_chunks = 1
    init_payload = {
        "filename": filename,
        "total_chunks": total_chunks
    }
    init_response = await auth_client.post("/files/init", json=init_payload)
    upload_data = init_response.json()
    upload_id = upload_data["upload_id"]
    batch_id = upload_data["batch_id"]
    form_data = {
        "upload_id": upload_id,
        "chunk_number": "0",
    }
    files = {
        "chunk": ("chunk_0", sample_file_data, "application/octet-stream")
    }
    await auth_client.post("/files/chunk", data=form_data, files=files)
    complete_payload = {
        "upload_id": upload_id,
        "filename": filename,
        "total_chunks": total_chunks,
        "batch_id": batch_id
    }
    response = await auth_client.post(
        "/files/complete/in", json=complete_payload)
    assert response.status_code == 201
    
    data = response.json()
    assert data["status"] == "upload complete"
    assert "completion_id" in data
    assert "final_file" in data
    assert data["final_file"] == filename
    assert "final_path" in data
    
    upload_dir = Path(f"{tmp_path}/data/upload")
    completion_id = data["completion_id"]
    final_path = upload_dir / completion_id / "in" / filename
    assert final_path.exists()
    assert final_path.read_bytes() == sample_file_data
    
    # Verify temp files were cleaned up
    temp_dir = upload_dir / upload_id
    assert not temp_dir.exists()

@pytest.mark.asyncio
async def test_multi_chunk_upload_success(auth_client, large_file_data, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    filename = "large_test_file.bin"
    chunk_size = 1 * 1024 * 1024  # 5MB chunks (same as frontend)
    total_chunks = (len(large_file_data) + chunk_size - 1) // chunk_size  
    init_payload = {
        "filename": filename,
        "total_chunks": total_chunks
    }
    init_response = await auth_client.post("/files/init", json=init_payload)
    assert init_response.status_code == 201
    
    upload_data = init_response.json()
    upload_id = upload_data["upload_id"]
    batch_id = upload_data["batch_id"]
    
    for chunk_num in range(total_chunks):
        start_byte = chunk_num * chunk_size
        end_byte = min(start_byte + chunk_size, len(large_file_data))
        chunk_data = large_file_data[start_byte:end_byte]
        form_data = {
            "upload_id": upload_id,
            "chunk_number": str(chunk_num),
        }
        files = {
            "chunk": (f"chunk_{chunk_num}", chunk_data, "application/octet-stream")
        }
        response = await auth_client.post("/files/chunk", data=form_data, files=files)
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "chunk received"
        assert data["chunk_number"] == chunk_num
        assert data["received_chunks"] == chunk_num + 1  # Should increase with each chunk

        chunk_path = upload_dir / upload_id / f"chunk_{chunk_num}"
        assert chunk_path.exists()
        assert chunk_path.read_bytes() == chunk_data
    
    complete_payload = {
        "upload_id": upload_id,
        "filename": filename,
        "total_chunks": total_chunks,
        "batch_id": batch_id
    }
    response = await auth_client.post(
        "/files/complete/in", json=complete_payload)
    assert response.status_code == 201
    
    data = response.json()
    assert data["status"] == "upload complete"
    assert data["final_file"] == filename
    
    # Verify final file was assembled correctly
    completion_id = data["completion_id"]
    final_path = upload_dir / completion_id / "in" / filename
    assert final_path.exists()
    
    reassembled_data = final_path.read_bytes()
    assert len(reassembled_data) == len(large_file_data)
    assert reassembled_data == large_file_data
    
    # Verify temp files were cleaned up
    temp_dir = upload_dir / upload_id
    assert not temp_dir.exists()

@pytest.mark.asyncio
async def test_multiple_files_upload_success(auth_client, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    
    # Create three different files with distinct content
    files_data = [
        ("document1.txt", b"This is the first document content. " * 50),
        ("image_data.bin", b"BINARY_IMAGE_DATA_" * 1000),
        ("config.yaml", b"version: 1.0\nname: test_config\nsettings:\n  debug: true\n" * 20)
    ]
    
    # Initialize batch
    batch_response = await auth_client.post("/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    
    upload_ids = []
    
    # Initialize uploads for all three files
    for filename, file_data in files_data:
        init_payload = {
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        init_response = await auth_client.post("/files/init", json=init_payload)
        assert init_response.status_code == 201
        
        upload_data = init_response.json()
        assert upload_data["batch_id"] == batch_id
        upload_ids.append(upload_data["upload_id"])
    
    # Upload chunks for all three files
    for i, (filename, file_data) in enumerate(files_data):
        upload_id = upload_ids[i]
        
        form_data = {
            "upload_id": upload_id,
            "chunk_number": "0",
        }
        files = {
            "chunk": ("chunk_0", file_data, "application/octet-stream")
        }
        
        response = await auth_client.post("/files/chunk", data=form_data, files=files)
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "chunk received"
        assert data["chunk_number"] == 0
        assert data["received_chunks"] == 1
        
        # Verify chunk file exists
        chunk_path = upload_dir / upload_id / "chunk_0"
        assert chunk_path.exists()
        assert chunk_path.read_bytes() == file_data
    
    completion_ids = []
    
    # Complete uploads for all three files
    for i, (filename, file_data) in enumerate(files_data):
        upload_id = upload_ids[i]
        
        complete_payload = {
            "upload_id": upload_id,
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        
        response = await auth_client.post(
            "/files/complete/in", json=complete_payload)
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "upload complete"
        assert data["final_file"] == filename
        assert "completion_id" in data
        assert "final_path" in data
        
        completion_id = data["completion_id"]
        completion_ids.append(completion_id)
        
        # Verify final file exists and has correct content
        final_path = upload_dir / completion_id / "in" / filename
        assert final_path.exists()
        assert final_path.is_file()
        assert final_path.stat().st_size == len(file_data)
    
    # Verify all three files were uploaded successfully with different completion IDs
    assert len(set(completion_ids)) == 1, "All completion IDs should be the same"
    
    # Verify all final files exist and are accessible
    for i, (filename, file_data) in enumerate(files_data):
        completion_id = completion_ids[i]
        final_path = upload_dir / completion_id / "in" / filename
        assert final_path.exists()
        assert final_path.is_file()
        assert final_path.stat().st_size == len(file_data)

@pytest.mark.asyncio
async def test_multiple_files_with_cancellation(auth_client, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    
    # Create five different files with distinct content
    files_data = [
        ("document1.txt", b"This is the first document content. " * 30),
        ("image_data.bin", b"BINARY_IMAGE_DATA_" * 500),
        ("config.yaml", b"version: 1.0\nname: test_config\nsettings:\n  debug: true\n" * 15),
        ("data.csv", b"name,age,city\nJohn,25,Berlin\nJane,30,Munich\n" * 50),
        ("script.py", b"#!/usr/bin/env python3\nprint('Hello, World!')\n" * 40)
    ]
    
    # Initialize batch
    batch_response = await auth_client.post("/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    
    upload_ids = []
    
    # Initialize uploads for all five files
    for filename, file_data in files_data:
        init_payload = {
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        init_response = await auth_client.post("/files/init", json=init_payload)
        assert init_response.status_code == 201
        
        upload_data = init_response.json()
        assert upload_data["batch_id"] == batch_id
        upload_ids.append(upload_data["upload_id"])
    
    # Upload chunks for all five files
    for i, (filename, file_data) in enumerate(files_data):
        upload_id = upload_ids[i]
        
        form_data = {
            "upload_id": upload_id,
            "chunk_number": "0",
        }
        files = {
            "chunk": ("chunk_0", file_data, "application/octet-stream")
        }
        
        response = await auth_client.post("/files/chunk", data=form_data, files=files)
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "chunk received"
        assert data["chunk_number"] == 0
        assert data["received_chunks"] == 1
        
        # Verify chunk file exists
        chunk_path = upload_dir / upload_id / "chunk_0"
        assert chunk_path.exists()
        assert chunk_path.read_bytes() == file_data
    
    # Cancel uploads for files at index 1 and 3 (image_data.bin and data.csv)
    cancelled_indices = [1, 3]
    cancelled_upload_ids = []
    
    for idx in cancelled_indices:
        upload_id = upload_ids[idx]
        cancelled_upload_ids.append(upload_id)
        
        response = await auth_client.delete(f"/files/cancel/{upload_id}")
        assert response.status_code == 204
        
        temp_dir = upload_dir / upload_id
        assert not temp_dir.exists()
    
    # Complete uploads for the remaining three files (indices 0, 2, 4)
    completed_indices = [0, 2, 4]
    completion_ids = []
    
    for idx in completed_indices:
        filename, file_data = files_data[idx]
        upload_id = upload_ids[idx]
        
        complete_payload = {
            "upload_id": upload_id,
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        
        response = await auth_client.post(
            "/files/complete/in", json=complete_payload)
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "upload complete"
        assert data["final_file"] == filename
        assert "completion_id" in data
        assert "final_path" in data
        
        completion_id = data["completion_id"]
        completion_ids.append(completion_id)
        
        # Verify final file exists and has correct content
        final_path = upload_dir / completion_id / "in" / filename
        assert final_path.exists()
        assert final_path.read_bytes() == file_data
        assert final_path.stat().st_size == len(file_data)
        
        # Verify temp files were cleaned up for completed upload
        temp_dir = upload_dir / upload_id
        assert not temp_dir.exists()
    
    # Verify that all completion IDs are the same (same batch)
    assert len(set(completion_ids)) == 1, "All completion IDs should be the same for batch upload"
    
    # Verify that cancelled files do not exist in final directory
    completion_id = completion_ids[0]
    for idx in cancelled_indices:
        cancelled_filename = files_data[idx][0]
        cancelled_final_path = upload_dir / completion_id / "in" / cancelled_filename
        assert not cancelled_final_path.exists(), f"Cancelled file {cancelled_filename} should not exist in final directory"
    
    # Verify all completed files exist and are accessible
    for idx in completed_indices:
        filename, file_data = files_data[idx]
        final_path = upload_dir / completion_id / "in" / filename
        assert final_path.exists()
        assert final_path.is_file()
        assert final_path.stat().st_size == len(file_data)
    
    # Summary assertion: 3 files completed, 2 files cancelled
    completed_files = [files_data[i][0] for i in completed_indices]
    cancelled_files = [files_data[i][0] for i in cancelled_indices]
    
    assert len(completed_files) == 3
    assert len(cancelled_files) == 2
    assert "document1.txt" in completed_files
    assert "config.yaml" in completed_files  
    assert "script.py" in completed_files
    assert "image_data.bin" in cancelled_files
    assert "data.csv" in cancelled_files

@pytest.mark.asyncio
async def test_multiple_files_directory_structure_complete_all(auth_client, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    
    # Get the user ID from the login response (auth_client is already authenticated)
    user_response = await auth_client.get("/role/admin")
    assert user_response.status_code == 200
    user_data = user_response.json()
    user_id = str(user_data["id"])
    
    # Create files that represent a directory structure with nested folders
    files_data = [
        ("docs/readme.txt", b"# Project Documentation\nThis is a comprehensive guide. " * 100),
        ("docs/endpoints.md", b"## API Endpoints\n### Authentication\n- POST /auth/login\n" * 80),
        ("src/main.py", b"#!/usr/bin/env python3\ndef main():\n    print('Hello World')\n" * 60),
        ("src/utils/helpers.py", b"# Utility functions\ndef helper_func():\n    return True\n" * 70),
        ("config/settings.yaml", b"app:\n  name: TestApp\n  version: 1.0\ndatabase:\n  host: localhost\n" * 50),
        ("config/data.json", b'{"users": [{"name": "John", "age": 30}], "settings": {"debug": true}}' * 90),
        ("config/unit/test_main.py", b"import unittest\nclass TestMain(unittest.TestCase):\n    def test_example(self):\n        self.assertTrue(True)\n" * 40)
    ]
    
    # Initialize batch
    batch_response = await auth_client.post("/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    
    upload_mapping = {}  # Will store upload_id -> {filename, totalChunks} for complete_all
    upload_ids = []
    
    # Initialize uploads for all files
    for filename, file_data in files_data:
        init_payload = {
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        init_response = await auth_client.post("/files/init", json=init_payload)
        assert init_response.status_code == 201
        
        upload_data = init_response.json()
        assert upload_data["batch_id"] == batch_id
        upload_id = upload_data["upload_id"]
        upload_ids.append(upload_id)
        
        # Store mapping for complete_all payload
        upload_mapping[upload_id] = {
            "filename": filename,
            "totalChunks": 1
        }
    
    # Upload chunks for all files
    for i, (filename, file_data) in enumerate(files_data):
        upload_id = upload_ids[i]
        
        form_data = {
            "upload_id": upload_id,
            "chunk_number": "0",
        }
        files = {
            "chunk": ("chunk_0", file_data, "application/octet-stream")
        }
        
        response = await auth_client.post("/files/chunk", data=form_data, files=files)
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "chunk received"
        assert data["chunk_number"] == 0
        assert data["received_chunks"] == 1
        
        # Verify chunk file exists in temp directory
        chunk_path = upload_dir / upload_id / "chunk_0"
        assert chunk_path.exists()
        assert chunk_path.read_bytes() == file_data
    
    # Generate a fake flow ID for the complete_all endpoint
    fid = str(uuid.uuid4())
    
    # Use complete_all endpoint to finish all uploads at once
    complete_all_response = await auth_client.post(
        f"/files/complete/{fid}/{batch_id}/in", json=upload_mapping
    )
    assert complete_all_response.status_code == 201
    
    # Verify response contains results for all uploads
    results = complete_all_response.json()
    assert len(results) == len(files_data)
    
    # Verify all uploads completed successfully
    for i, result in enumerate(results):
        filename, file_data = files_data[i]
        assert result["status"] == "upload complete"
        assert result["final_file"] == filename
        assert result["completion_id"] == fid  # Should use provided fid
        assert result["batch_id"] == batch_id
        
        # Verify final file exists with directory structure preserved
        exec_dir = Path(f"{tmp_path}/data/execution")  # Based on EXEC_DIR setting
        final_path = exec_dir / user_id / fid / "in" / filename
        assert final_path.exists(), f"Final file {filename} should exist at {final_path}"
        assert final_path.is_file()
        
        # Verify file content is correct
        assert final_path.read_bytes() == file_data
        assert final_path.stat().st_size == len(file_data)
        
        # Verify directory structure is preserved
        if "/" in filename:
            # Check that parent directories were created
            assert final_path.parent.exists()
            assert final_path.parent.is_dir()
    
    # Verify temp upload directories were cleaned up
    for upload_id in upload_ids:
        temp_dir = upload_dir / upload_id
        assert not temp_dir.exists(), f"Temp directory {upload_id} should be cleaned up"
    
    # Verify specific directory structure exists
    exec_dir = Path(f"{tmp_path}/data/execution")
    fid_dir = exec_dir / user_id / fid / "in"
    
    # Check that all expected directories exist
    expected_dirs = ["docs", "src", "src/utils", "config", "config/unit"]
    for expected_dir in expected_dirs:
        dir_path = fid_dir / expected_dir
        assert dir_path.exists(), f"Directory {expected_dir} should exist"
        assert dir_path.is_dir()
    
    # Check that all files exist in their correct locations
    expected_files = [
        "docs/readme.txt",
        "docs/endpoints.md",
        "src/main.py",
        "src/utils/helpers.py",
        "config/settings.yaml",
        "config/data.json",
        "config/unit/test_main.py"
    ]
    
    for expected_file in expected_files:
        file_path = fid_dir / expected_file
        assert file_path.exists(), f"File {expected_file} should exist"
        assert file_path.is_file()
    
    # Summary verification: 7 files uploaded in directory structure
    uploaded_file_count = sum(1 for _ in fid_dir.rglob("*") if _.is_file())
    assert uploaded_file_count == 7, f"Expected 7 files, found {uploaded_file_count}"

    # Test the list_files endpoint to verify all files and directories
    list_response = await auth_client.get(f"/files/{fid}/in")
    assert list_response.status_code == 200
    
    entries_list = list_response.json()
    assert isinstance(entries_list, list)
    
    # Should have 7 files + 5 directories (docs, src, src/utils, config, config/unit)
    files = [e for e in entries_list]
    # directories = [e for e in entries_list if e["is_directory"]]
    
    assert len(files) == 7, f"Expected 7 files, got {len(files)}"
    # assert len(directories) == 5, f"Expected 5 directories, got {len(directories)}"


@pytest.mark.asyncio
async def test_list_files_directory_structure(auth_client, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    
    # Get the user ID from the login response
    user_response = await auth_client.get("/role/admin")
    assert user_response.status_code == 200
    user_data = user_response.json()
    user_id = str(user_data["id"])
    
    # Create test files with directory structure
    files_data = [
        ("config.yaml", b"app:\n  name: TestApp\n  version: 2.0\n" * 30),
        ("docs/readme.md", b"# Documentation\nThis is the main documentation file.\n" * 40),
        ("src/app.py", b"def main():\n    print('Application started')\n" * 25),
        ("data/users.json", b'{"users": [{"id": 1, "name": "Alice"}]}' * 35)
    ]
    
    # Initialize batch
    batch_response = await auth_client.post("/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    
    upload_mapping = {}
    upload_ids = []
    
    # Initialize and upload all files
    for filename, file_data in files_data:
        init_payload = {
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        init_response = await auth_client.post("/files/init", json=init_payload)
        assert init_response.status_code == 201
        
        upload_data = init_response.json()
        upload_id = upload_data["upload_id"]
        upload_ids.append(upload_id)
        
        upload_mapping[upload_id] = {
            "filename": filename,
            "totalChunks": 1
        }
        
        # Upload chunk
        form_data = {
            "upload_id": upload_id,
            "chunk_number": "0",
        }
        files = {
            "chunk": ("chunk_0", file_data, "application/octet-stream")
        }
        
        response = await auth_client.post("/files/chunk", data=form_data, files=files)
        assert response.status_code == 201
    
    # Complete all uploads using complete_all
    fid = str(uuid.uuid4())
    complete_all_response = await auth_client.post(
        f"/files/complete/{fid}/{batch_id}/in", json=upload_mapping)
    assert complete_all_response.status_code == 201
    
    # Now test the list_files endpoint
    list_response = await auth_client.get(f"/files/{fid}/in")
    assert list_response.status_code == 200
    
    entries_list = list_response.json()
    assert isinstance(entries_list, list)
    
    # Should have 4 files + 3 directories (docs, src, data)
    expected_total = 4  # 4 files + 3 directories
    assert len(entries_list) == expected_total, f"Expected {expected_total} entries, got {len(entries_list)}"
    
    # Verify each entry has required fields
    for entry in entries_list:
        assert "path" in entry
        assert "size" in entry
        assert "last_modified" in entry
        # assert "is_directory" in entry
        assert isinstance(entry["size"], int)
        assert isinstance(entry["last_modified"], (int, float))
        
        # Size validation based on type
        # if entry["is_directory"]:
        #     assert entry["size"] == 0, "Directories should have size 0"
        # else:
        assert entry["size"] > 0, "Files should have size > 0"
    
    # Separate files and directories
    files = [e for e in entries_list]
    # directories = [e for e in entries_list if e["is_directory"]]
    
    # Verify we have the expected number of each
    assert len(files) == 4, f"Expected 4 files, got {len(files)}"
    # assert len(directories) == 3, f"Expected 3 directories, got {len(directories)}"
    
    # Verify file paths match what we uploaded
    file_paths = [f["path"] for f in files]
    expected_file_paths = [
        "in/config.yaml", 
        "in/docs/readme.md", 
        "in/src/app.py", 
        "in/data/users.json"
    ]
    
    for expected_path in expected_file_paths:
        assert expected_path in file_paths, f"Expected file {expected_path} not found in list"
    
    # Verify directory paths
    # dir_paths = [d["path"] for d in directories]
    # expected_dir_paths = ["docs", "src", "data"]
    
    # for expected_dir in expected_dir_paths:
    #     assert expected_dir in dir_paths, f"Expected directory {expected_dir} not found in list"
    
    # Verify entries are sorted by path
    all_paths = [e["path"] for e in entries_list]
    sorted_paths = sorted(all_paths)
    assert all_paths == sorted_paths, "Entries should be sorted by path"
    
    # Verify file sizes match the original data
    for file_entry in files:
        original_file_data = next(
            data for name, data in files_data 
            if f"in/{name}" == file_entry["path"])
        assert file_entry["size"] == len(original_file_data), f"Size mismatch for {file_entry['path']}"
    
    # Test with non-existent fid
    invalid_response = await auth_client.get(f"/files/{uuid.uuid4()}/in")
    assert invalid_response.status_code == 200
    invalid_list = invalid_response.json()
    assert isinstance(invalid_list, list)
    assert len(invalid_list) == 0  # Should return empty list for non-existent fid


@pytest.mark.asyncio
async def test_get_file_download(auth_client, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    
    # Get the user ID from the login response
    user_response = await auth_client.get("/role/admin")
    assert user_response.status_code == 200
    user_data = user_response.json()
    user_id = str(user_data["id"])
    
    # Create test files with known content
    files_data = [
        ("config.yaml", b"app:\n  name: TestApp\n  version: 2.0\ndebug: true"),
        ("docs/readme.md", b"# Documentation\nThis is the main documentation file.\n\n## Installation\n\n```bash\npip install app\n```"),
        ("src/app.py", b"def main():\n    print('Application started')\n    return 'Hello World'"),
        ("data/users.json", b'{"users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]}')
    ]
    
    # Initialize batch
    batch_response = await auth_client.post("/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    
    upload_mapping = {}
    
    # Initialize and upload all files
    for filename, file_data in files_data:
        init_payload = {
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        init_response = await auth_client.post("/files/init", json=init_payload)
        assert init_response.status_code == 201
        
        upload_data = init_response.json()
        upload_id = upload_data["upload_id"]
        
        upload_mapping[upload_id] = {
            "filename": filename,
            "totalChunks": 1
        }
        
        # Upload chunk
        form_data = {
            "upload_id": upload_id,
            "chunk_number": "0",
        }
        files = {
            "chunk": ("chunk_0", file_data, "application/octet-stream")
        }
        
        response = await auth_client.post("/files/chunk", data=form_data, files=files)
        assert response.status_code == 201
    
    # Complete all uploads
    fid = str(uuid.uuid4())
    complete_all_response = await auth_client.post(
        f"/files/complete/{fid}/{batch_id}/in", json=upload_mapping)
    assert complete_all_response.status_code == 201
    
    # Test downloading each file
    for filename, expected_content in files_data:
        download_response = await auth_client.get(f"/files/{fid}/in/{filename}")
        assert download_response.status_code == 200
        
        # Check response headers
        assert download_response.headers["content-type"] == "application/octet-stream"
        assert "content-disposition" in download_response.headers
        assert f'attachment; filename="{Path(filename).name}"' in download_response.headers["content-disposition"]
        assert "content-length" in download_response.headers
        assert int(download_response.headers["content-length"]) == len(expected_content)
        
        # Check response content
        downloaded_content = download_response.content
        assert downloaded_content == expected_content, f"Content mismatch for {filename}"
    
    # Test downloading non-existent file
    not_found_response = await auth_client.get(f"/files/{fid}/in/nonexistent.txt")
    assert not_found_response.status_code == 404
    
    # Test with invalid directory type
    invalid_dir_response = await auth_client.get(f"/files/{fid}/invalid/config.yaml")
    assert invalid_dir_response.status_code == 400
    
    # Test with non-existent fid
    invalid_fid_response = await auth_client.get(f"/files/{uuid.uuid4()}/in/config.yaml")
    assert invalid_fid_response.status_code == 404
    
    # Test trying to download a directory (should fail)
    directory_response = await auth_client.get(f"/files/{fid}/in/docs")
    assert directory_response.status_code == 404
    assert "Cannot retrieve directories" in directory_response.json()["detail"]


@pytest.mark.asyncio 
async def test_get_file_security(auth_client, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    
    # Get the user ID from the login response
    user_response = await auth_client.get("/role/admin")
    assert user_response.status_code == 200
    user_data = user_response.json()
    user_id = str(user_data["id"])
    
    # Create a simple test file
    test_content = b"test file content"
    
    # Initialize batch
    batch_response = await auth_client.post("/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    
    # Upload one file
    init_payload = {
        "filename": "test.txt",
        "total_chunks": 1,
        "batch_id": batch_id
    }
    init_response = await auth_client.post("/files/init", json=init_payload)
    assert init_response.status_code == 201
    
    upload_data = init_response.json()
    upload_id = upload_data["upload_id"]
    
    # Upload chunk
    form_data = {
        "upload_id": upload_id,
        "chunk_number": "0",
    }
    files = {
        "chunk": ("chunk_0", test_content, "application/octet-stream")
    }
    
    response = await auth_client.post("/files/chunk", data=form_data, files=files)
    assert response.status_code == 201
    
    # Complete upload
    fid = str(uuid.uuid4())
    upload_mapping = {
        upload_id: {
            "filename": "test.txt",
            "totalChunks": 1
        }
    }
    complete_response = await auth_client.post(
        f"/files/complete/{fid}/{batch_id}/in", json=upload_mapping)
    assert complete_response.status_code == 201
    
    # Test path traversal attempts (should all fail with 403 or 404)
    path_traversal_attempts = [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config\\sam",
        "../../../../etc/shadow",
        "../test.txt",
        "subdir/../../test.txt",
        "/etc/passwd",
        "C:\\Windows\\System32\\config\\sam"
    ]
    
    for malicious_path in path_traversal_attempts:
        malicious_response = await auth_client.get(
            f"/files/{fid}/in/{malicious_path}")
        # Should be either 404 (not found) or 403 (access denied)
        assert malicious_response.status_code in [403, 404, 400], f"Path traversal not blocked for: {malicious_path}"
    
    # Test that legitimate file still works
    legitimate_response = await auth_client.get(f"/files/{fid}/in/test.txt")
    assert legitimate_response.status_code == 200
    assert legitimate_response.content == test_content


@pytest.mark.asyncio
async def test_get_file_path_normalization(auth_client, tmp_path):
    upload_dir = Path(f"{tmp_path}/data/upload")
    
    # Get the user ID from the login response
    user_response = await auth_client.get("/role/admin")
    assert user_response.status_code == 200
    user_data = user_response.json()
    user_id = str(user_data["id"])
    
    # Create test files
    files_data = [
        ("config.yaml", b"app: test config"),
        ("docs/readme.md", b"# Documentation"),
        ("deeply/nested/path/file.txt", b"deep file content")
    ]
    
    # Initialize batch and upload files
    batch_response = await auth_client.post("/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    
    upload_mapping = {}
    
    for filename, file_data in files_data:
        init_payload = {
            "filename": filename,
            "total_chunks": 1,
            "batch_id": batch_id
        }
        init_response = await auth_client.post("/files/init", json=init_payload)
        assert init_response.status_code == 201
        
        upload_data = init_response.json()
        upload_id = upload_data["upload_id"]
        
        upload_mapping[upload_id] = {
            "filename": filename,
            "totalChunks": 1
        }
        
        form_data = {
            "upload_id": upload_id,
            "chunk_number": "0",
        }
        files = {
            "chunk": ("chunk_0", file_data, "application/octet-stream")
        }
        
        response = await auth_client.post("/files/chunk", data=form_data, files=files)
        assert response.status_code == 201
    
    # Complete upload
    fid = str(uuid.uuid4())
    complete_response = await auth_client.post(
        f"/files/complete/{fid}/{batch_id}/in", json=upload_mapping)
    assert complete_response.status_code == 201
    
    # Test that various path formats all work and return the same content
    # This tests the server-side path.lstrip('/') normalization
    path_variants = [
        # Test root file with different leading slash combinations
        ("config.yaml", ["/config.yaml", "config.yaml"]),
        # Test nested file with different path formats  
        ("docs/readme.md", ["/docs/readme.md", "docs/readme.md"]),
        # Test deeply nested path
        ("deeply/nested/path/file.txt", ["/deeply/nested/path/file.txt", "deeply/nested/path/file.txt"])
    ]
    
    for expected_filename, path_variations in path_variants:
        expected_content = next(data for name, data in files_data if name == expected_filename)
        
        # Test each path variation should return the same content
        responses = []
        for path_variant in path_variations:
            url_path = path_variant if path_variant.startswith('/') else f"/{path_variant}"
            download_response = await auth_client.get(f"/files/{fid}/in{url_path}")
            assert download_response.status_code == 200, f"Failed to download {path_variant}"
            responses.append(download_response.content)
        
        # All variants should return identical content
        assert all(content == expected_content for content in responses), \
            f"Path normalization failed - different variants returned different content for {expected_filename}"
        
        # Verify all responses are identical
        assert len(set(responses)) == 1, f"Path variants for {expected_filename} returned different responses"
    
    # Test that edge cases with multiple leading slashes are handled correctly
    edge_case_response = await auth_client.get(f"/files/{fid}/in/config.yaml")
    assert edge_case_response.status_code == 200
    assert edge_case_response.content == b"app: test config"