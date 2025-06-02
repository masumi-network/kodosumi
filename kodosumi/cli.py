import subprocess
import sys
from pathlib import Path
import click
import psutil
import ray
from pathlib import Path
from kodosumi.core import __version__
import kodosumi.service.server
import kodosumi.spooler
from kodosumi import helper
from kodosumi.config import LOG_LEVELS, Settings
from kodosumi.log import spooler_logger
from kodosumi.runner.const import NAMESPACE
import kodosumi.ops
import json as jsonlib

@click.group()
@click.version_option(version=__version__, prog_name="kodosumi")
def cli():
    """Kodosumi CLI tool."""
    pass

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
@click.option('--start/--stop', is_flag=True, default=True, 
              help='Run spooler.')
@click.option('--block', is_flag=True, default=False, 
              help='Run spooler in foreground (blocking mode).')
@click.option('--status', is_flag=True, default=False, 
              help='Check if spooler is connected and running.')
def spooler(ray_server, log_file, log_file_level, level, exec_dir, interval,
            batch_size, timeout, start, block, status):
    
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
    spooler_logger(settings)

    if status:
        try:
            helper.ray_init(settings)
            spooler = ray.get_actor("Spooler", namespace=NAMESPACE)
            meta = ray.get(spooler.get_meta.remote())
            pid = meta.get("pid")
            current = meta.get("active")
            total = meta.get("total")
        except Exception as e:
            print(f"spooler actor not found.")
        else:
            try:
                proc = psutil.Process(meta.get("pid"))
                active = proc.is_running()
            except Exception as e:
                active = False
            print(f"spooler (pid={pid}) is{'' if active else ''} running")
            print(f"active flows: {current}, total flows: {total}")
        return
    if start:
        if block:
            kodosumi.spooler.main(settings)
        else:
            cmd = [sys.executable, "-m", "kodosumi.cli"]
            cmd += sys.argv[1:] + ["--block"]
            proc = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True
            )
            click.echo(f"spooler started (pid={proc.pid}).")
    else:
        kodosumi.spooler.terminate(settings)


@cli.command("serve")
@click.option("--address", default=None, 
              help="App server URL")
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
           uvicorn_level, register):
    kw = {}
    if address: kw["APP_SERVER"] = address
    if log_file: kw["APP_LOG_FILE"] = log_file
    if log_file_level: kw["APP_LOG_FILE_LEVEL"] = log_file_level
    if level: kw["APP_STD_LEVEL"] = level
    if exec_dir: kw["EXEC_DIR"] = exec_dir
    if reload: kw["APP_RELOAD"] = reload
    if uvicorn_level: kw["UVICORN_LEVEL"] = uvicorn_level
    if register: kw["REGISTER_FLOW"] = register

    settings = Settings(**kw)
    kodosumi.service.server.run(settings)


@cli.command("start")
@click.option('--exec-dir', default=None, 
              help='Execution directory.')
@click.option("--register", multiple=True, help="Register endpoints")

def server(exec_dir, register):
    kw = {}
    if exec_dir: kw["EXEC_DIR"] = exec_dir
    if register: kw["REGISTER_FLOW"] = register

    settings = Settings(**kw)
    try:
        cmd = [sys.executable, "-m", "kodosumi.cli", "spool"]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait()
        kodosumi.service.server.run(settings)
    except Exception as e:
        print(e)
        sys.exit(1)
        


@cli.command("deploy")
@click.option('-f', '--file', type=str, required=False, default=None, help='config YAML file')
@click.option('-r', '--run', is_flag=True, default=False, help='run deployment')
@click.option('-d', '--dry', '--dry-run', is_flag=True, default=False, help='test deployment configuration')
@click.option('-x', '--shutdown', '--stop', is_flag=True, default=False, help='shutdown serve')
@click.option('-s', '--status', is_flag=True, default=False, help='serve status')
@click.option('-j', '--json', is_flag=True, default=False, help='render output as json')
def deploy(file: str, run: bool, dry: bool, shutdown: bool, status: bool, json: bool):

    settings = Settings()

    def _exit(message: str):
        print(message)
        sys.exit(-1)

    if shutdown:
        if run or status or dry:
            _exit("--shutdown cannot be used together with --run, --status or --dry")
        kodosumi.ops.shutdown()

    if run or dry:
        if status:
            _exit("--run/--dry-run cannot be used together with --status")
        if not file:
            file = settings.YAML_BASE
        f = Path(file)
        if f.exists() and f.is_file():
            if run:
                kodosumi.ops.deploy(str(f))
            else:
                kodosumi.ops.build_config(str(f))
        else:
            _exit(f"config file {f} not found")

    if status or not (shutdown or run or dry):
        s = kodosumi.ops.status()
        if json:
            print(jsonlib.dumps(s, indent=2))
        else:
            print("serve status:")
            if s:
                print("\n".join([f"- {k}: {v}" for k, v in sorted(s.items())]))
            else:
                print("- inactive")


if __name__ == "__main__":
    cli()
