import sys
import os
import signal
import subprocess

#
# this is a helper script to kill all python multiprocessing processes
# which might leak and cause problems during development
# processes are children of the root process because the parent process is gone
#
# use with caution and only if you know what you are doing
#

procs = []
process_list = subprocess.check_output(['ps', 'aux']).decode('utf-8').split('\n')
for process in process_list:
    process = process.lower()
    if 'python' in process and ('multiproc' in process or 'debugpy' in process):
    # if 'python' in process and 'debugpy' in process:
        procs.append(process)

if procs:
    print("""
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
KILLS ALL PYTHON DEBUGPY & MULTIPROCESSING PROCESSES
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
HANDLE WITH CARE AND USE ONLY IF YOU KNOW WHAT YOU ARE DOING
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    """)
    print(f"Found {len(procs)} python processes")
    print()
    if sys.argv[-1] != "force":
        input("continue? (press enter)")

    for process in procs:
        pid = int(process.split()[1])
        os.kill(pid, signal.SIGKILL)
        print(f"Killed process with PID: {pid}")

else:
    print("nothing to do, all good.")