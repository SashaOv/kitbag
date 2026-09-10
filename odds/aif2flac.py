#!/bin/env python3

import argparse
import os
import re
import subprocess
import sys


REGEX = re.compile("(.*?)\\.aif")


def run(dirs, out):
    for dir in dirs:
        if not out:
            out = dir
        for file in os.listdir(dir):
            matched = REGEX.match(file)
            if matched:
                cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-y",
                    "-i",
                    os.path.join(dir, file),
                    os.path.join(out, matched.group(1) + ".flac"),
                ]
                print(">", cmd)
                subprocess.call(cmd)

def cli():
    parser = argparse.ArgumentParser(
        description="Convert .aif files to FLAC using ffmpeg"
    )
    parser.add_argument("dir", nargs="*", help="directory where files are located")
    parser.add_argument("--out", nargs="?", help="output directory", default=None)
    args = parser.parse_args()
    dirs = args.dir
    if len(dirs) > 0:
        run(dirs, args.out)
    else:
        print("Too few arguments", file=sys.stderr)
        parser.print_help()


if __name__ == "__main__":
    cli()