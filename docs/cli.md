# CLI Commands

The Kodosumi CLI (`koco`) provides various commands for managing and running Kodosumi services.

## Overview

```bash
koco --version  # Shows version
koco --help     # Shows help for all commands
```

## Commands

### spool

Manages the Spooler service, which is responsible for processing flows.

```bash
koco spool [OPTIONS]
```

**Options:**

- `--ray-server TEXT` - Ray Server URL
- `--log-file TEXT` - Spooler log file path
- `--log-file-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]` - Spooler log file level
- `--level [DEBUG|INFO|WARNING|ERROR|CRITICAL]` - Screen log level
- `--exec-dir TEXT` - Execution directory
- `--interval FLOAT` - Spooler polling interval (min: 0)
- `--batch-size INTEGER` - Batch size for spooling (min: 1)
- `--timeout FLOAT` - Batch retrieval timeout (min: 0)
- `--start/--stop` - Start/stop spooler (default: start)
- `--block` - Run spooler in foreground (blocking mode)
- `--status` - Check if spooler is connected and running

**Examples:**

```bash
# Start spooler
koco spool

# Check spooler status
koco spool --status

# Run spooler in foreground with custom configuration
koco spool --block --interval 5.0 --batch-size 10
```

### serve

Starts the Kodosumi panel API and application.

```bash
koco serve [OPTIONS]
```

**Options:**

- `--address TEXT` - App server URL
- `--log-file TEXT` - App server log file path
- `--log-file-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]` - App server log file level
- `--level [DEBUG|INFO|WARNING|ERROR|CRITICAL]` - Screen log level
- `--uvicorn-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]` - Uvicorn log level
- `--exec-dir TEXT` - Execution directory
- `--reload` - Reload app server on file changes
- `--register TEXT` - Register endpoints (can be used multiple times)

**Examples:**

```bash
# Start server
koco serve

# Start server with reload and custom address
koco serve --reload --address 0.0.0.0:8000

# Start server with flow registration
koco serve --register flow1 --register flow2
```

### start

Starts both the spooler and app server in a single execution.

```bash
koco start [OPTIONS]
```

**Options:**

- `--exec-dir TEXT` - Execution directory
- `--register TEXT` - Register endpoints (can be used multiple times)

**Examples:**

```bash
# Start complete service
koco start

# With custom execution directory
koco start --exec-dir /path/to/flows
```

### deploy

Manages deployments of Kodosumi configurations.

```bash
koco deploy [OPTIONS]
```

**Options:**

- `-f, --file TEXT` - Configuration YAML file
- `-r, --run` - Execute deployment
- `-d, --dry-run` - Test deployment configuration
- `-x, --shutdown, --stop` - Shutdown service
- `-s, --status` - Show service status
- `-j, --json` - Format output as JSON

**Examples:**

```bash
# Check deployment status
koco deploy --status

# Test deployment configuration
koco deploy --dry-run -f config.yaml

# Execute deployment
koco deploy --run -f config.yaml

# Shutdown service
koco deploy --shutdown

# Output status as JSON
koco deploy --status --json
```

## Configuration

The CLI commands use the same configuration options as the Kodosumi application. These can be set via environment variables or command line parameters.

### Important environment variables:

- `RAY_SERVER` - Ray Server URL
- `EXEC_DIR` - Execution directory
- `SPOOLER_INTERVAL` - Spooler polling interval
- `SPOOLER_BATCH_SIZE` - Batch size
- `APP_SERVER` - App server address
- `APP_RELOAD` - Enable reload mode

## Logging

All commands support configurable logging levels:

- `DEBUG` - Detailed debug information
- `INFO` - General information
- `WARNING` - Warnings
- `ERROR` - Errors
- `CRITICAL` - Critical errors

Logs can be output both to screen and to files. 