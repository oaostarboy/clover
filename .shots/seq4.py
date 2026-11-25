import subprocess,sys,time
font=sys.argv[1]; out=sys.argv[2]; steps=sys.argv[3].split(",")
fa=["-fa",font,"-fs","13"] if font!="x" else []
p=subprocess.Popen(["xterm","-T","s4","-xrm","XTerm*allowSendEvents: true","-bg","#0d1210","-fg","#e6ece8","-geometry","100x40+100+100"]+fa+["-e","bash","/tmp/qs.sh"])
time.sleep(8)
wid=subprocess.check_output(["xdotool","search","--name","s4"]).split()[0].decode()
n=0
for s in steps:
    if s.startswith("shot"):
        subprocess.run(["import","-window",wid,out%s[4:]])
    elif s.startswith("type:"):
        subprocess.run(["xdotool","type","--delay","80","--window",wid,s[5:]]); time.sleep(.6)
    elif s.startswith("sleep:"): time.sleep(float(s[6:]))
    else:
        subprocess.run(["xdotool","key","--window",wid,s]); time.sleep(1.2)
p.terminate()
