# event stream kinds:
from kodosumi.config import InternalSettings


EVENT_META    = "meta"  # flow metadata and entry point information
EVENT_INPUTS  = "inputs" # user input data
EVENT_AGENT   = "agent" # agent information
EVENT_UPLOAD  = "upload" # user file upload data

EVENT_DEBUG   = "debug" # debug message
EVENT_STDOUT  = "stdout" # stdout information
EVENT_STDERR  = "stderr" # stderr information

EVENT_LOCK    = "lock" # lock information
EVENT_LEASE   = "lease" # lease information

EVENT_STATUS  = "status" # flow status change
EVENT_ERROR   = "error" # error information
EVENT_ACTION  = "action" # action information
EVENT_RESULT  = "result" # task result information
EVENT_FINAL   = "final" # final result information
MAIN_EVENTS = (EVENT_META, EVENT_INPUTS, EVENT_AGENT, EVENT_STATUS, 
               EVENT_ERROR, EVENT_ACTION, EVENT_RESULT, EVENT_FINAL,
               EVENT_LOCK, EVENT_LEASE)
STDIO_EVENTS = (EVENT_ERROR, EVENT_STDOUT, EVENT_STDERR, EVENT_DEBUG, 
                EVENT_UPLOAD)
# flow status and lifecycle:
STATUS_STARTING = "starting"
STATUS_RUNNING  = "running"
STATUS_AWAITING = "awaiting"
STATUS_END      = "finished"
STATUS_ERROR    = "error"
STATUS_FINAL    = (STATUS_END, STATUS_ERROR)

NAMESPACE = "kodosumi"
SPOOLER_NAME = "Spooler"
KODOSUMI_LAUNCH = "kodosumi_launch"
DB_FILE = "sqlite3.db"
DB_FILE_WAL = "sqlite3.db-wal"
DB_FILE_SHM = "sqlite3.db-shm"
DB_ARCHIVE = ".archive"
SLEEP = 0.4
AFTER = 10
PING = 3.
CHECK_ALIVE = 15
STATUS_TEMPLATE = "status/status.html"
FORM_TEMPLATE = "form.html"
STATUS_REDIRECT = "/admin/status/view/{fid}"
TOKEN_KEY = "kodosumi_jwt"
HEADER_KEY = "KODOSUMI_API_KEY"
DEFAULT_TIME_DELTA = 86400  # 1 day in seconds
ALGORITHM = "HS256"
JWT_SECRET = InternalSettings().SECRET_KEY
KODOSUMI_USER = "x-kodosumi_user"
KODOSUMI_BASE = "x-kodosumi_base"
KODOSUMI_API = "x-kodosumi"
KODOSUMI_AUTHOR = "x-author"
KODOSUMI_URL = "x-kodosumi-url"
KODOSUMI_ORGANIZATION = "x-organization"