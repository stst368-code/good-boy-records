"""Run the current song studio regression at desktop and phone widths."""
import argparse
from pathlib import Path
import subprocess
import sys

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',required=True)
    parser.add_argument('--width',type=int,action='append')
    args=parser.parse_args()
    for width in args.width or [1440,3840,390,320]:
        subprocess.run([sys.executable,str(Path(__file__).with_name('ui_song_studio.py')),
                        '--browser',args.browser,'--width',str(width)],check=True)

if __name__=='__main__':main()
