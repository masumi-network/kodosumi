import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any, Dict, List

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

    tags = ["Files"]
    
    # Konstanten für bessere Wartbarkeit
    VALID_DIR_TYPES = ["in", "out"]
    CHUNK_PREFIX = "chunk_"

    def upload_dir(self, state: State) -> Path:
        return Path(state.settings.UPLOAD_DIR.rstrip('/'))

    def _validate_dir_type(self, dir_type: str) -> None:
        """Validiert den Verzeichnistyp und wirft HTTPException bei ungültigem Typ"""
        if dir_type not in self.VALID_DIR_TYPES:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid directory type. Must be one of: {', '.join(self.VALID_DIR_TYPES)}"
            )

    def _get_exec_dir(self, user: str, fid: str, dir_type: str, state: State) -> Path:
        """Erstellt und validiert das Ausführungsverzeichnis"""
        self._validate_dir_type(dir_type)
        return Path(state.settings.EXEC_DIR) / user / fid / dir_type

    def _validate_file_path(self, file_path: Path, base_dir: Path, path: str) -> Path:
        """Validiert einen Dateipfad für Sicherheit und Existenz"""
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
        """Streamt eine Datei asynchron"""
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                while chunk := await f.read(state.settings.SAVE_CHUNK_SIZE):
                    yield chunk
                    await asyncio.sleep(0)
        except Exception as e:
            logger.error(f"Error streaming file {file_path}: {str(e)}")
            raise HTTPException(status_code=500, detail="Error reading file")

    async def _cleanup_session(self, upload_id: str, state: State) -> None:
        """Bereinigt eine Upload-Session"""
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
        """Check filesystem to determine current upload status"""
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

    @post("/init_batch")
    async def init_batch(self) -> dict:
        batch_id = str(uuid.uuid4())
        return {"batch_id": batch_id}

    @post("/init")
    async def init_upload(self, data: UploadInit, state: State) -> dict:
        upload_id = str(uuid.uuid4())
        session_dir = self.upload_dir(state) / upload_id
        session_dir.mkdir(parents=True)        
        return {
            "upload_id": upload_id, 
            "batch_id": data.batch_id
        }

    @post("/chunk")
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
            async with aiofiles.open(chunk_path, 'wb') as f:
                while data_chunk := await data.chunk.read(state.settings.SAVE_CHUNK_SIZE):
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
            # Validierung
            self._validate_dir_type(dir_type)
            status = self.get_upload_status(state, upload_id)
            
            if not status["exists"]:
                return {"error": "Invalid upload ID"}
            
            if status["total_received"] != total_chunks:
                return {"error": "Not all chunks uploaded"}
            
            # Überprüfe fehlende Chunks
            missing_chunks = []
            for i in range(total_chunks):
                path = self.upload_dir(state) / f"{upload_id}/{self.CHUNK_PREFIX}{i}"
                if not path.exists():
                    missing_chunks.append(i)
            
            if missing_chunks:
                return {"error": f"Missing chunk files: {missing_chunks}"}
            
            # Bestimme Zielverzeichnis
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
            
            # Kombiniere Chunks zu finaler Datei
            async with aiofiles.open(final_path, 'wb') as final_file:
                for i in range(total_chunks):
                    path = self.upload_dir(state) / upload_id / f"{self.CHUNK_PREFIX}{i}"
                    try:
                        async with aiofiles.open(path, 'rb') as file:
                            while chunk := await file.read(state.settings.SAVE_CHUNK_SIZE):
                                await final_file.write(chunk)
                                await asyncio.sleep(0)
                    except Exception as e:
                        return {"error": f"Error reading chunk {i}: {str(e)}"}
            
            # Bereinige Session
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

    @post("/complete/{dir_type:str}")
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

    @post("/complete/{fid:str}/{batch_id:str}/{dir_type:str}")
    async def complete_all(self, 
                           fid: str,
                           batch_id: str,
                           dir_type: str,
                           request: Request,
                           state: State) -> List:
        result = []
        payload = await request.json()
        for upload_id, info in payload.items():
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

    @delete("/cancel/{upload_id:str}")
    async def cancel_upload(self, upload_id: str, state: State) -> None:
        try:
            session_dir = self.upload_dir(state) / upload_id
            if not session_dir.exists():
                raise RuntimeError(f"Invalid upload ID - upload not found")
            shutil.rmtree(session_dir)
        except Exception as e:
            raise RuntimeError(f"Error cancelling upload: {str(e)}")

    @get("/{fid:str}/{dir_type:str}")
    async def list_files(self, 
                         fid: str, 
                         dir_type: str,
                         request: Request, 
                         state: State) -> List[Dict[str, Any]]:
        exec_dir = self._get_exec_dir(request.user, fid, dir_type, state)
        
        if not exec_dir.exists():
            return []
        
        entries_list = []
        processed_dirs = set()
        
        # Sammle Dateien und Verzeichnisse
        for file_path in exec_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(exec_dir)
                file_size = file_path.stat().st_size
                last_modified = file_path.stat().st_mtime
                entries_list.append({
                    "path": str(relative_path),
                    "size": file_size,
                    "last_modified": last_modified,
                    "is_directory": False
                })
                
                # Sammle übergeordnete Verzeichnisse
                current_parent = relative_path.parent
                while current_parent != Path("."):
                    processed_dirs.add(str(current_parent))
                    current_parent = current_parent.parent
        
        # Füge Verzeichnisse hinzu
        for dir_path_str in processed_dirs:
            dir_full_path = exec_dir / dir_path_str
            if dir_full_path.exists() and dir_full_path.is_dir():
                last_modified = dir_full_path.stat().st_mtime
                entries_list.append({
                    "path": dir_path_str,
                    "size": 0,
                    "last_modified": last_modified,
                    "is_directory": True
                })
        
        entries_list.sort(key=lambda x: str(x["path"]))
        return entries_list

    @get("/{fid:str}/{dir_type:str}/{path:path}")
    async def get_file(self, 
                       fid: str, 
                       dir_type: str, 
                       path: str, 
                       request: Request, 
                       state: State) -> Stream:
        try:
            exec_dir = self._get_exec_dir(request.user, fid, dir_type, state)
            
            if not exec_dir.exists():
                raise NotFoundException(f"Directory '{dir_type}' not found for flow {fid}")
            
            normalized_path = path.lstrip('/')
            file_path = exec_dir / normalized_path
            
            # Validiere Pfad
            file_path = self._validate_file_path(file_path, exec_dir, path)
            
            # Überprüfe Dateityp
            if file_path.is_dir():
                raise HTTPException(status_code=400, detail="Cannot retrieve directories")
            
            if not file_path.is_file():
                raise HTTPException(status_code=400, detail="Path does not point to a valid file")
            
            file_size = file_path.stat().st_size
            filename = file_path.name
            
            return Stream(
                content=self._stream_file(file_path, state),
                media_type="application/octet-stream",
                headers={
                    "Content-Length": str(file_size),
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "no-cache"
                }
            )
        except (NotFoundException, HTTPException):
            raise
        except Exception as e:
            logger.error(
                f"Error retrieving file {path} from {dir_type} directory "
                f"for fid {fid}, user {request.user}: {str(e)}")
            raise HTTPException(
                status_code=500, 
                detail="Internal server error while retrieving file")

    @delete("/{fid:str}/{dir_type:str}/{path:path}")
    async def delete_file(self, 
                          fid: str, 
                          dir_type: str, 
                          path: str, 
                          request: Request, 
                          state: State) -> None:
        base_dir = self._get_exec_dir(request.user, fid, dir_type, state)
        target_path = (base_dir / path.strip("/")).resolve()
        
        if not str(target_path).startswith(str(base_dir.resolve())):
            raise HTTPException(status_code=403, detail="Forbidden")
        
        if not target_path.exists():
            raise NotFoundException(detail=f"Path not found: {path}")
        
        try:
            if target_path.is_dir():
                shutil.rmtree(target_path)
            elif target_path.is_file():
                os.remove(target_path)
            else:
                raise HTTPException(status_code=400, detail="Path is not a file or directory")
        except OSError as e:
            logger.error(f"Error deleting path {target_path}: {e}")
            raise HTTPException(status_code=500, detail=f"Error deleting path: {e.strerror}")
