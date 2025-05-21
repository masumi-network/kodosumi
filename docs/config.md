# kodosumi configuration and settings

This document describes all available configuration settings for kodosumi. Settings can be configured through environment variables, a `.env` file, or command-line arguments.

## Environment Variables

All configuration settings can be set using environment variables with the prefix `KODO_`. For example, to set the execution directory, use `KODO_EXEC_DIR`. These environment variables can be overridden using the `koco` command-line tool.

## Configuration Settings

### Execution Settings

- `EXEC_DIR` (default: `"./data/execution"`)
  - Directory where execution files are stored
  - Will be created automatically if it doesn't exist
  - `koco` option `--exec-dir` (available in all commands)
    - Can override `KODO_EXEC_DIR` in all commands

### Spooler Settings

- `SPOOLER_LOG_FILE` (default: `"./data/spooler.log"`)
  - Path to the spooler log file
  - Parent directory will be created automatically
  - `koco` option `--log-file` (spool command)
    - Can override `KODO_SPOOLER_LOG_FILE` in spool command

- `SPOOLER_LOG_FILE_LEVEL` (default: `"DEBUG"`)
  - Log level for the spooler log file
  - Available levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  - `koco` option `--log-file-level` (spool command)
    - Can override `KODO_SPOOLER_LOG_FILE_LEVEL` in spool command

- `SPOOLER_STD_LEVEL` (default: `"INFO"`)
  - Log level for spooler console output
  - Available levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  - `koco` option `--level` (spool command)
    - Can override `KODO_SPOOLER_STD_LEVEL` in spool command

- `SPOOLER_INTERVAL` (default: `0.25`)
  - Polling interval for the spooler in seconds
  - Must be greater than 0
  - `koco` option `--interval` (spool command)
    - Can override `KODO_SPOOLER_INTERVAL` in spool command

- `SPOOLER_BATCH_SIZE` (default: `10`)
  - Number of items to process in each batch
  - Must be at least 1
  - `koco` option `--batch-size` (spool command)
    - Can override `KODO_SPOOLER_BATCH_SIZE` in spool command

- `SPOOLER_BATCH_TIMEOUT` (default: `0.1`)
  - Timeout for batch retrieval in seconds
  - Must be greater than 0
  - `koco` option `--timeout` (spool command)
    - Can override `KODO_SPOOLER_BATCH_TIMEOUT` in spool command

### Ray Settings

- `RAY_SERVER` (default: `"localhost:6379"`)
  - Ray server URL
  - `koco` option `--ray-server` (spool command)
    - Can override `KODO_RAY_SERVER` in spool command

- `RAY_DASHBOARD` (default: `"http://localhost:8265"`)
  - Ray dashboard URL

### Application Server Settings

- `APP_SERVER` (default: `"http://localhost:3370"`)
  - Application server URL
  - `koco` option `--address` (serve command)
    - Can override `KODO_APP_SERVER` in serve command

- `APP_LOG_FILE` (default: `"./data/app.log"`)
  - Path to the application log file
  - `koco` option `--log-file` (serve command)
    - Can override `KODO_APP_LOG_FILE` in serve command

- `APP_LOG_FILE_LEVEL` (default: `"DEBUG"`)
  - Log level for the application log file
  - Available levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  - `koco` option `--log-file-level` (serve command)
    - Can override `KODO_APP_LOG_FILE_LEVEL` in serve command

- `APP_STD_LEVEL` (default: `"INFO"`)
  - Log level for application console output
  - Available levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  - `koco` option `--level` (serve command)
    - Can override `KODO_APP_STD_LEVEL` in serve command

- `APP_RELOAD` (default: `False`)
  - Enable auto-reload on file changes
  - `koco` option `--reload` (serve command)
  - Can override `KODO_APP_RELOAD` in serve command

- `UVICORN_LEVEL` (default: `"WARNING"`)
  - Log level for Uvicorn server
  - Available levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  - `koco` option `--uvicorn-level` (serve command)
    - Can override `KODO_UVICORN_LEVEL` in serve command

### Security Settings

- `SECRET_KEY`
  - Secret key for the application
  - Should be changed in production

- `CORS_ORIGINS` (default: `["*"]`)
  - List of allowed CORS origins
  - Can be set as comma-separated string

### Database Settings

- `ADMIN_DATABASE` (default: `"sqlite+aiosqlite:///./data/admin.db"`)
  - Database URL for admin database

- `ADMIN_EMAIL`
  - Admin user email
  - Should be changed in production

- `ADMIN_PASSWORD` (default: `"admin"`)
  - Admin user password
  - Should be changed in production

### Other Settings

- `WAIT_FOR_JOB` (default: `600`)
  - Maximum wait time for jobs in seconds

- `PROXY_TIMEOUT` (default: `30`)
  - Proxy timeout in seconds


