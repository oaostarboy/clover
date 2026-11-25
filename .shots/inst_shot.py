import subprocess,time
p=subprocess.Popen(['xterm','-T','inst-demo','-fa','DejaVu Sans Mono','-fs','13','-bg','#0d1210','-fg','#e6ece8','-geometry','100x40+100+100','-e','bash','/tmp/inst_demo.sh'])
time.sleep(4)
w=subprocess.check_output(['xdotool','search','--name','inst-demo']).split()[0].decode()
subprocess.run(['import','-window',w,'v3/01_install.png']); p.terminate()
