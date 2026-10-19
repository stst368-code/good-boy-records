"""Reusable isolated processing setup, never modifies the native MiniMax environment."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv

ROOT=Path(__file__).parent

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cpu',action='store_true');args=parser.parse_args()
    python=ROOT/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    if not python.is_file():venv.EnvBuilder(with_pip=True).create(ROOT/'.venv')
    subprocess.run([str(python),'-m','pip','install','torch==2.8.0+'+('cpu' if args.cpu else 'cu128'),'torchaudio==2.8.0+'+('cpu' if args.cpu else 'cu128'),'torchvision==0.23.0+'+('cpu' if args.cpu else 'cu128'),'--index-url','https://download.pytorch.org/whl/'+('cpu' if args.cpu else 'cu128')],check=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    print('Processing environment ready. Models download on the first explicit alignment job. FFmpeg must be available on PATH.')

if __name__=='__main__':main()
