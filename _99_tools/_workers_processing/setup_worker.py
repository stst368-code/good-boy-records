"""Reusable isolated processing setup, never modifies the native MiniMax environment."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv

ROOT=Path(__file__).parent

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cpu',action='store_true')
    parser.add_argument('--cuda',choices=['cu126','cu128'],default='cu128',help='Use cu126 for GTX 10-series/Pascal cards.')
    args=parser.parse_args()
    python=ROOT/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    if not python.is_file():venv.EnvBuilder(with_pip=True).create(ROOT/'.venv')
    build='cpu' if args.cpu else args.cuda
    subprocess.run([str(python),'-m','pip','install','torch==2.8.0+'+build,'torchaudio==2.8.0+'+build,'torchvision==0.23.0+'+build,'--index-url','https://download.pytorch.org/whl/'+build],check=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    print('Processing environment ready. Models download on the first explicit alignment job. FFmpeg must be available on PATH.')

if __name__=='__main__':main()
