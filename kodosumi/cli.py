import click

import kodosumi.service.server
import kodosumi.spooler
from kodosumi.config import LOG_LEVELS, Settings


@click.group()
def cli():
    """CLI for managing kodosumi services."""


@cli.command("spool")
@click.option("--ray-server", default=None, 
              help="Ray server URL")
@click.option('--log-file', default=None, 
              help='Spooler log file path.')
@click.option('--log-file-level', default=None, 
              help='Spooler log file level.',
              type=click.Choice(LOG_LEVELS, case_sensitive=False))
@click.option('--level', default=None, 
              help='Screen log level.',
              type=click.Choice(LOG_LEVELS, case_sensitive=False))
@click.option('--exec-dir', default=None, 
              help='Execution directory.')
@click.option('--interval', default=None, 
              help='Spooler polling interval.',
              type=click.FloatRange(min=0, min_open=True))
@click.option('--batch-size', default=None, 
              help='Batch size for spooling.',
              type=click.IntRange(min=1))
@click.option('--timeout', default=None, 
              help='Batch retrieval timeout.',
              type=click.FloatRange(min=0, min_open=True))
@click.option('--force', is_flag=True, default=False, 
              help='Force spooler to start.')
def spooler(ray_server, log_file, log_file_level, level, exec_dir, interval,
            batch_size, timeout, force):
    """Start the kodosumi spooler."""
    kw = {}
    if ray_server: kw["RAY_SERVER"] = ray_server
    if log_file: kw["SPOOLER_LOG_FILE"] = log_file
    if log_file_level: kw["SPOOLER_LOG_FILE_LEVEL"] = log_file_level
    if level: kw["SPOOLER_STD_LEVEL"] = level
    if exec_dir: kw["EXEC_DIR"] = exec_dir
    if interval: kw["SPOOLER_INTERVAL"] = interval
    if batch_size: kw["SPOOLER_BATCH_SIZE"] = batch_size
    if timeout: kw["SPOOLER_BATCH_TIMEOUT"] = timeout
    settings = Settings(**kw)
    kodosumi.spooler.main(settings, force=force)


@cli.command("serve")
@click.option("--address", default=None, 
              help="App server URL")
@click.option("--ray-http", default=None, 
              help="Ray http server URL")
@click.option('--log-file', default=None, 
              help='App server log file path.')
@click.option('--log-file-level', default=None, 
              type=click.Choice(LOG_LEVELS, case_sensitive=False),
              help='App server log file level.')
@click.option('--level', default=None, 
              type=click.Choice(LOG_LEVELS, case_sensitive=False),
              help='Screen log level.')
@click.option('--uvicorn-level', default=None, 
              type=click.Choice(LOG_LEVELS, case_sensitive=False),
              help='Uvicorn log level.')
@click.option('--exec-dir', default=None, 
              help='Execution directory.')
@click.option('--reload', is_flag=True, 
              help='App server reload on file change.')
@click.option("--register", multiple=True, help="Register endpoints")

def server(address, log_file, log_file_level, level, exec_dir, reload,
           uvicorn_level, ray_http, register):
    """Start the kodosumi app service."""
    kw = {}
    if address: kw["APP_SERVER"] = address
    if log_file: kw["APP_LOG_FILE"] = log_file
    if log_file_level: kw["APP_LOG_FILE_LEVEL"] = log_file_level
    if level: kw["APP_STD_LEVEL"] = level
    if exec_dir: kw["EXEC_DIR"] = exec_dir
    if reload: kw["APP_RELOAD"] = reload
    if uvicorn_level: kw["UVICORN_LEVEL"] = uvicorn_level
    if ray_http: kw["RAY_HTTP"] = ray_http
    if register: kw["REGISTER_FLOW"] = register
    settings = Settings(**kw)
    kodosumi.service.server.run(settings)


if __name__ == "__main__":
    cli()
