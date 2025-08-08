import asyncio
import os
import shutil
import uuid
import urllib.parse
from pathlib import Path
from typing import Annotated, Any, Dict, List, Union

import aiofiles
import litestar
from litestar import Request, delete, get, post
from litestar.datastructures import State
from litestar.enums import RequestEncodingType
from litestar.exceptions import HTTPException, NotFoundException
from litestar.params import Body
from litestar.response import Stream

from kodosumi.dtypes import ChunkUpload, UploadComplete, UploadInit
from kodosumi.log import logger


class FileControl(litestar.Controller):

    tags = ["Files Management"]
    
    # Konstanten für bessere Wartbarkeit
    VALID_DIR_TYPES = ["in", "out"]
    CHUNK_PREFIX = "chunk_"

    def upload_dir(self, state: State) -> Path:
        return Path(state.settings.UPLOAD_DIR.rstrip('/'))

    def _validate_dir_type(self, dir_type: str) -> None:
        if dir_type not in self.VALID_DIR_TYPES:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid directory type. Must be one of: "
                       f"{', '.join(self.VALID_DIR_TYPES)}"
            )

    def _get_exec_dir(self, 
                      user: str, 
                      fid: str, 
                      dir_type: str, 
                      state: State) -> Path:
        self._validate_dir_type(dir_type)
        return Path(state.settings.EXEC_DIR) / user / fid / dir_type

    def _validate_file_path(self, 
                            file_path: Path, 
                            base_dir: Path, 
                            path: str) -> Path:
        try:
            file_path = file_path.resolve()
            base_dir = base_dir.resolve()
            if not file_path.is_relative_to(base_dir):
                raise HTTPException(
                    status_code=403, 
                    detail="Access denied: path outside allowed directory"
                )
        except (OSError, ValueError):
            raise NotFoundException(f"Invalid file path: {path}")
        
        if not file_path.exists():
            raise NotFoundException(f"File not found: {path}")
        
        return file_path

    async def _stream_file(self, file_path: Path, state: State):
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                while chunk := await f.read(state.settings.SAVE_CHUNK_SIZE):
                    yield chunk
                    await asyncio.sleep(0)
        except Exception as e:
            logger.error(f"Error streaming file {file_path}: {str(e)}")
            raise HTTPException(status_code=500, detail="Error reading file")

    async def _cleanup_session(self, upload_id: str, state: State) -> None:
        try:
            session_dir = self.upload_dir(state) / upload_id
            if session_dir.exists():
                for file in session_dir.iterdir():
                    if file.is_file():
                        os.remove(file)
                os.rmdir(session_dir)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    def get_upload_status(self, state: State, upload_id: str) -> dict:
        session_dir = self.upload_dir(state) / upload_id
        if not session_dir.exists():
            return {"exists": False, "received_chunks": []}
        
        received_chunks = []
        for chunk_file in session_dir.iterdir():
            if chunk_file.name.startswith(self.CHUNK_PREFIX):
                chunk_num = int(chunk_file.name.split("_")[1])
                received_chunks.append(chunk_num)
        
        return {
            "exists": True,
            "received_chunks": sorted(received_chunks),
            "total_received": len(received_chunks)
        }

    def generate_completion_id(self, batch_id: str | None = None) -> str:
        return batch_id if batch_id else str(uuid.uuid4())

    @post("/init_batch", summary="Initialize Batch Upload",
          description="Initialize a new batch upload session.", operation_id="70_init_batch")
    async def init_batch(self) -> dict:
        batch_id = str(uuid.uuid4())
        return {"batch_id": batch_id}

    @post("/init", summary="Initialize Single Upload",
          description="Initialize a new single upload session.", operation_id="71_init_upload")
    async def init_upload(self, data: UploadInit, state: State) -> dict:
        upload_id = str(uuid.uuid4())
        session_dir = self.upload_dir(state) / upload_id
        session_dir.mkdir(parents=True)        
        return {
            "upload_id": upload_id, 
            "batch_id": data.batch_id
        }

    @post("/chunk", summary="Upload File Chunk",
          description="Upload a chunk of a file.", operation_id="72_upload_chunk")
    async def upload_chunk(self,
                           data: Annotated[
                               ChunkUpload, 
                               Body(media_type=RequestEncodingType.MULTI_PART)
                           ],
                           state: State) -> dict:
        session_dir = self.upload_dir(state) / data.upload_id
        if not session_dir.exists():
            return {"error": "Invalid upload ID - upload not initialized"}
        
        chunk_path = session_dir / f"{self.CHUNK_PREFIX}{data.chunk_number}"
        if not chunk_path.exists():
            # Validate chunk size - maximum 1MB per chunk
            MAX_CHUNK_SIZE = state.settings.SAVE_CHUNK_SIZE
            total_bytes_written = 0
            
            async with aiofiles.open(chunk_path, 'wb') as f:
                while data_chunk := await data.chunk.read(
                        state.settings.SAVE_CHUNK_SIZE):
                    chunk_size = len(data_chunk)
                    total_bytes_written += chunk_size
                    
                    # Check if total chunk size exceeds maximum
                    if total_bytes_written > MAX_CHUNK_SIZE:
                        # Clean up the partial file
                        await f.close()
                        if chunk_path.exists():
                            chunk_path.unlink()
                        raise HTTPException(
                            status_code=413, 
                            detail=f"Chunk size exceeds maximum allowed size of {MAX_CHUNK_SIZE} bytes. "
                                   f"Received chunk size: {total_bytes_written} bytes"
                        )
                    
                    await f.write(data_chunk)
                    await asyncio.sleep(0)
        
        status = self.get_upload_status(state, data.upload_id)
        logger.info(f"upload complete {chunk_path}")
        return {
            "status": "chunk received",
            "chunk_number": data.chunk_number,
            "received_chunks": status["total_received"]
        }

    async def _complete_file(self, 
                             user: str,
                             fid: str,
                             batch_id: str,
                             upload_id: str, 
                             dir_type: str,
                             filename: str,
                             total_chunks: int, 
                             state: State) -> dict:
        try:
            self._validate_dir_type(dir_type)
            status = self.get_upload_status(state, upload_id)
            if not status["exists"]:
                return {"error": "Invalid upload ID"}
            if status["total_received"] != total_chunks:
                return {"error": "Not all chunks uploaded"}
            missing_chunks = []
            for i in range(total_chunks):
                path = self.upload_dir(state)
                path = path / f"{upload_id}/{self.CHUNK_PREFIX}{i}"
                if not path.exists():
                    missing_chunks.append(i)
            if missing_chunks:
                return {"error": f"Missing chunk files: {missing_chunks}"}
            if fid:
                completion_id = fid
                completion_dir = Path(state.settings.EXEC_DIR) / user / fid
            else:
                completion_id = self.generate_completion_id(batch_id)
                completion_dir = self.upload_dir(state) / completion_id
            
            completion_dir.mkdir(parents=True, exist_ok=True)
            final_path = completion_dir / dir_type / filename
            final_path.parent.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"complete upload {filename}")
            
            async with aiofiles.open(final_path, 'wb') as final_file:
                for i in range(total_chunks):
                    chunk_name = f"{self.CHUNK_PREFIX}{i}"
                    path = self.upload_dir(state) / upload_id / chunk_name
                    try:
                        async with aiofiles.open(path, 'rb') as file:
                            while chunk := await file.read(
                                    state.settings.SAVE_CHUNK_SIZE):
                                await final_file.write(chunk)
                                await asyncio.sleep(0)
                    except Exception as e:
                        return {"error": f"Error reading chunk {i}: {str(e)}"}
            await self._cleanup_session(upload_id, state)
            return {
                "status": "upload complete", 
                "completion_id": completion_id,
                "batch_id": batch_id,
                "final_file": filename,
                "final_path": f"{completion_id}/{filename}"
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"Internal server error: {str(e)}"}

    @post("/complete/{dir_type:str}", summary="Complete Single File Upload",
          description="Complete the upload of a single file with `dir_type` as `in` or `out`.", operation_id="73_complete_upload")
    async def complete_upload(self, 
                              request: Request,
                              data: UploadComplete, 
                              dir_type: str,
                              state: State) -> dict:
        return await self._complete_file(
            request.user,
            data.fid or "",
            data.batch_id or "",
            data.upload_id,
            dir_type,
            data.filename,
            data.total_chunks,
            state)

    @post("/complete/{fid:str}/{batch_id:str}/{dir_type:str}", 
          summary="Complete Batch Upload",
          description="Complete the upload of a batch of files with `dir_type` as `in` or `out`.", operation_id="74_complete_batch")
    async def complete_all(self, 
                           fid: str,
                           batch_id: str,
                           dir_type: str,
                           request: Request,
                           state: State) -> List:
        result = []
        payload = await request.json()
        unique = set()
        for upload_id, info in payload.items():
            if info["filename"] in unique:
                continue
            unique.add(info["filename"])
            result.append(
                await self._complete_file(
                    request.user,
                    fid,
                    batch_id,
                    upload_id,
                    dir_type,
                    info["filename"],
                    info["totalChunks"],
                    state))
        return result

    @delete("/cancel/{upload_id:str}", summary="Cancel a single File Upload",
            description="Cancel a single file upload session.", operation_id="75_cancel_upload")
    async def cancel_upload(self, upload_id: str, state: State) -> None:
        try:
            session_dir = self.upload_dir(state) / upload_id
            if not session_dir.exists():
                raise RuntimeError(f"Invalid upload ID - upload not found")
            shutil.rmtree(session_dir)
        except Exception as e:
            raise RuntimeError(f"Error cancelling upload: {str(e)}")

    @get("/{fid:str}/{path:path}", summary="Retrieve a single File",
          description="Retrieve a single file from the execution `in` or `out` directory.", operation_id="76_get_files")
    async def get_files(self, 
                        fid: str, 
                        path: str,
                        request: Request, 
                        state: State) -> Union[List[Dict[str, Any]], Stream]:
        dir_type, *sub_path = path.lstrip("/").split("/", 1)
        self._validate_dir_type(dir_type)
        exec_dir = self._get_exec_dir(request.user, fid, dir_type, state)
        if sub_path:
            # retrieve file
            file = exec_dir / "/".join(sub_path)
            if not file.exists():
                raise NotFoundException(f"'{file}' not found for flow {fid}")
            if file.is_dir():
                raise NotFoundException("Cannot retrieve directories")
            size = file.stat().st_size
            filename = file.name
            encoded_filename = urllib.parse.quote(filename)
            return Stream(
                content=self._stream_file(file, state),
                media_type="application/octet-stream",
                headers={
                    "Content-Length": str(size),
                    "Content-Disposition": f'attachment; filename="{encoded_filename}"; filename*=UTF-8\'\'{encoded_filename}',
                    "Cache-Control": "no-cache",
                    "Content-Type": "application/octet-stream"
                }
            )
        else:
            # retrieve folder
            if not exec_dir.exists():
                return []
            entries_list = []
            for file in exec_dir.rglob("*"):
                if file.is_file():
                    relative_path = file.relative_to(exec_dir.parent)
                    size = file.stat().st_size
                    last_modified = file.stat().st_mtime
                    entries_list.append({
                        "path": str(relative_path),
                        "size": size,
                        "last_modified": last_modified
                    })
            entries_list.sort(key=lambda x: str(x["path"]))
            return entries_list

    @delete("/{fid:str}/{path:path}", summary="Delete a file or directory",
            description="Delete a file or directory from the execution `in` or `out` directory.", operation_id="77_delete_file")
    async def delete_file(self, 
                          fid: str, 
                          path: str, 
                          request: Request, 
                          state: State) -> None:
        root, *sub_path = path.lstrip("/").split("/", 1)
        if root not in self.VALID_DIR_TYPES:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid directory type. Must be one of: "
                       f"{', '.join(self.VALID_DIR_TYPES)}"
            )
        _path = "/".join(sub_path).rstrip("/")
        base_dir = self._get_exec_dir(request.user, fid, root, state)
        target_path = (base_dir / _path.strip("/")).resolve()
        if not target_path.exists():
            raise NotFoundException(detail=f"Path not found: {path}")
        if target_path.is_dir():
            shutil.rmtree(target_path)
        elif target_path.is_file():
            os.remove(target_path)
