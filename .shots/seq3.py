# usage: seq.py prefix "wait:key,wait:key..." ; shots after each step
import subprocess,sys,time,os
prefix=sys.argv[1]; steps=sys.argv[2].split(",")
args=sys.argv[3:] 
cmd=os.path.dirname(os.path.abspath(__file__))+"/run3.sh "+" ".join(args)
p=subprocess.Popen(["xterm","-T","seq-"+prefix,"-fa","DejaVu Sans Mono","-fs","13","-xrm","XTerm*allowSendEvents: true","-bg","#0d1210","-fg","#e6ece8","-geometry","100x40+100+100","-e","bash","-c",cmd])
time.sleep(7)
wid=subprocess.check_output(["xdotool","search","--name","seq-"+prefix]).split()[0].decode()
n=0
for s in steps:
    if s=="shot":
        n+=1; subprocess.run(["import","-window",wid,f"v3/{prefix}_{n}.png"])
    elif s.startswith("type:"):
        subprocess.run(["xdotool","type","--window",wid,s[5:]]); time.sleep(.5)
    elif s.startswith("sleep:"): time.sleep(float(s[6:]))
    else:
        subprocess.run(["xdotool","key","--window",wid,s]); time.sleep(1.5)
p.terminate()
