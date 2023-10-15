#!/usr/bin/env python

import sys
import time
import subprocess

period= float(sys.argv[1])
command= sys.argv[2:]
counter= 1
while True:
    print("{}> {}".format(counter, " ".join(command)))
    counter+=1
    subprocess.call(command)
    time.sleep(period)