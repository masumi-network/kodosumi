from pydantic import RootModel
from typing import Dict, Any, Union, Literal, Optional
import datetime
from pathlib import Path
from pprint import pprint
import sqlite3
import time
from enum import Enum as PyEnum

from pydantic import BaseModel
from kodosumi.dtypes import DynamicModel
from kodosumi.runner import const
from kodosumi.log import logger


fromUnix = datetime.datetime.fromtimestamp

# Pydantic Enum für MODES
class MODES(str, PyEnum):
    NEXT = "next"
    UPDATE = "update"

FIELDS = ("fid", "tags", "summary", "description", "author", "organization", 
          "version", "final", "inputs", "status", "startup", "finish", 
          "search")

META_FIELDS = ("fid", "tags", "summary", "description", "author",
               "organization", "version")

DELIVER_FIELDS = ("fid", "tags", "summary", "inputs", "status", "startup",
                  "finish", "runtime")
SEARCH_FIELDS = ("author", "organization", "summary", "description", "fid", 
                 "status")

def get_time(cursor):
    query = "SELECT min(timestamp), max(timestamp) FROM monitor"
    cursor.execute(query)   
    ret = cursor.fetchone()
    start = end = None
    if ret:
        start = fromUnix(ret[0]) if ret[0] else None
        end = fromUnix(ret[1]) if ret[1] else None
    if start and end:
        runtime = (end - start).total_seconds()
    else:
        runtime = None
    return start, end, runtime

def get_status(cursor):
    # select latest status        
    query = """
    SELECT message FROM monitor 
    WHERE kind = 'status' 
    ORDER BY id DESC 
    LIMIT 1
    """
    cursor.execute(query)   
    ret = cursor.fetchone()
    if ret and ret[0]:
        return ret[0]
    return None

def build_search(result):
    search = []
    for key in SEARCH_FIELDS:
        if result[key]:
            search.append(f"{key}:{result[key]}")
    if result["tags"]:
        for tag in result["tags"]:
            search.append(f"tag:{tag}")
    if result["startup"]:
        search.append(
            f"startup:{result['startup'].isoformat()}")
    if result["inputs"]:
        search.append(f"inputs:{result["inputs"]}")
    if result["final"]:
        search.append(f"final:{result['final']}")
    return " ".join(search).lower()

def load_result(filename):
    conn = sqlite3.connect(filename)
    cursor = conn.cursor()
    startup, finish, runtime = get_time(cursor)
    status = get_status(cursor)
    query = """
    SELECT kind, message FROM monitor 
    WHERE kind IN ('inputs', 'meta', 'final')
    ORDER BY id DESC
    """
    cursor.execute(query)
    result = {k: None for k in FIELDS}
    result["status"] = status
    result["startup"] = startup
    result["finish"] = finish
    result["runtime"] = runtime
    for rec in cursor.fetchall():
        kind, message = rec
        data = DynamicModel.model_validate_json(message)
        if kind == "meta":
            for key in META_FIELDS:
                result[key] = data.root["dict"][key]
        elif kind in ("final", "inputs"):
            result[kind] = data.root
    return result

def load_page(root: Union[Path, str], 
              mode: Optional[MODES]=MODES.NEXT,
              origin: Optional[str]=None, 
              offset: Optional[str]=None, 
              timestamp: Optional[float]=None,
              pp: int=10, 
              query: Optional[str]=None):
    current_timestamp = time.time()    
    all_dirs = []
    root = Path(root)
    if root.exists():
        all_dirs = [d for d in root.iterdir() if d.is_dir()]    
    all_dirs.sort(key=lambda d: d.name, reverse=True)
    total = len(all_dirs)
    if origin is None and all_dirs:
        origin = all_dirs[0].name  # origin is newest fid
    append_items: list[dict] = []
    insert_items: list[dict] = []
    update_items: list[dict] = []
    next_offset = offset

    def _load(db_file, match=True):
        fid = db_file.parent.name
        try:
            result = load_result(db_file)
            result["fid"] = fid
            if result.get("status"):
                if query and match:
                    search = build_search(result)
                    if query.lower() not in search:
                        return None
                return {
                    k: v for k, v in result.items() 
                    if k in DELIVER_FIELDS
                }
            return None
        except Exception as e:
            logger.error(f"failed to load {db_file}: {e}")

    for dir_path in all_dirs:
        fid = dir_path.name
        db_file = dir_path / const.DB_FILE
        if not db_file.exists():
            continue
        # case 1: new element
        if origin and fid > origin:
            logger.debug(f"new execution: {fid}")
            rec = _load(db_file, match=True)
            if rec:
                insert_items.append(rec)
                continue
        # case 2: modified element
        mod_time = db_file.stat().st_mtime
        for supp in (dir_path / const.DB_FILE_WAL, 
                     dir_path / const.DB_FILE_SHM):
            if supp.exists():
                mod_time = max(mod_time, supp.stat().st_mtime)
        if timestamp and mod_time > timestamp:
            logger.debug(f"modified execution: {fid}")
            rec = _load(db_file, match=False)
            update_items.append(rec)
            continue        
        # case 3: elements for current page request
        if mode == "next" and (next_offset is None or fid < next_offset):
            if len(append_items) < pp:
                rec = _load(db_file, match=True)
                if rec:
                    append_items.append(rec)
                next_offset = fid
            if len(append_items) >= pp:
                break
    # if mode == "next" and append_items:
    #     updated_offset = next_offset
    # else:
    #     updated_offset = offset
    if insert_items:
        sorted_inserts = sorted(
            insert_items, key=lambda x: x["fid"], reverse=True)
        new_origin = sorted_inserts[0]["fid"]
    else:
        new_origin = origin
    if append_items or insert_items or update_items:
        timestamp = current_timestamp
    result = {
        "total": total,
        "origin": new_origin,
        "offset": next_offset if mode == MODES.NEXT and append_items else offset,
        "timestamp": timestamp,
        "items": {
            "append": append_items,
            "insert": insert_items,
            "update": update_items
        },
        "query": query
    }
    
    return result

