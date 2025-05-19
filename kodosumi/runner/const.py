# event stream kinds:
EVENT_META    = "meta"  # flow metadata and entry point information
EVENT_INPUTS  = "inputs" # user input data
EVENT_AGENT   = "agent" # agent information

EVENT_DEBUG   = "debug" # debug message
EVENT_STDOUT  = "stdout" # stdout information
EVENT_STDERR  = "stderr" # stderr information

EVENT_STATUS  = "status" # flow status change
EVENT_ERROR   = "error" # error information
EVENT_ACTION  = "action" # action information
EVENT_RESULT  = "result" # task result information
EVENT_FINAL   = "final" # final result information
MAIN_EVENTS = (EVENT_META, EVENT_INPUTS, EVENT_AGENT, EVENT_STATUS, 
               EVENT_ERROR, EVENT_ACTION, EVENT_RESULT, EVENT_FINAL)
STDIO_EVENTS = (EVENT_ERROR, EVENT_STDOUT, EVENT_STDERR, EVENT_DEBUG)
# flow status and lifecycle:
STATUS_STARTING = "starting"
STATUS_RUNNING  = "running"
STATUS_END      = "finished"
STATUS_ERROR    = "error"
STATUS_FINAL    = (STATUS_END, STATUS_ERROR)

NAMESPACE = "kodosumi"
KODOSUMI_LAUNCH = "kodosumi_launch"
DB_FILE = "sqlite3.db"
DB_FILE_WAL = "sqlite3.db-wal"
DB_FILE_SHM = "sqlite3.db-shm"
DB_ARCHIVE = ".archive"