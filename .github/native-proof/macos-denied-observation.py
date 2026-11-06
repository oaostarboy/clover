"""Read-only native diagnostic; no inferred permission to skip processes."""
import json, os, sys, time
from pathlib import Path
import psutil
if sys.platform != 'darwin' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise SystemExit('disposable hosted Mac required')
output = Path(sys.argv[1]); rows=[]
for p in psutil.process_iter():
    try:
        u=p.uids()
        if os.geteuid() not in (u.real,u.effective): continue
        try: p.cmdline(); continue
        except psutil.AccessDenied: pass
        row={'pid':p.pid, 'uid':list(u), 'observations':[]}
        for attempt in range(2):
            item={'attempt':attempt}
            for name in ['ppid','create_time','status','name','exe','cmdline']:
                try: item[name]=getattr(p,name)()
                except (psutil.Error,OSError) as e: item[name]={'error':type(e).__name__}
            row['observations'].append(item)
            if attempt == 0: time.sleep(0.1)
        rows.append(row)
    except psutil.NoSuchProcess: continue
    except psutil.AccessDenied: rows.append({'pid':p.pid,'uid_error':'AccessDenied'})
output.write_text(json.dumps({'kind':'read-only-observation-not-acceptance','rows':rows},indent=2))
print('Recorded denied process observations:',len(rows))
