"""All mandatory IDs; no cloud access on import or plan display."""
STEPS = [
 ('R-01',1,480,'stop','Installer success; agent disabled/inactive; collectors active'),
 ('R-02',1,240,'stop','Permission check exit 0; new Gateway ready and normal 200'),
 ('N-03',1,120,'continue','25 labtools Python files; preflight command works with fail=0'),
 ('N-01',1,120,'stop','Active feed; namespace argument; fresh healthy snapshots; no argument error'),
 ('R-03',1,480,'continue','Exactly 31 cases, all passed'),
 ('R-04',1,900,'critical-auth','AWS-01,02,03,04,05,06,12 all passed; comparison objects restored'),
 ('R-05',1,600,'continue','AWS-04,05,09 passed; direct TCP/tokenize timeout; Hubble dropped; before/after 200'),
 ('R-06',1,60,'continue','TG-CODE default render contains kernel identity data without intent override'),
 ('R-07',1,300,'continue','60 normal HTTP 200; zero raw TG-CODE events in exact window'),
 ('R-08',2,180,'continue','Harmless .py create/import: detected, inferring, confirmed'),
 ('R-09',1,900,'restore','Two approved false keys match; mm flag true drift/automatic config_drift; remove and match/200'),
 ('R-10',1,240,'continue','Real vLLM 200 content and usage preserved; SSE reaches DONE'),
 ('N-02',1,180,'continue','Direct mvp + target has nonempty Cilium values; wrong context refuses startup'),
 ('N-04',1,300,'restore','Direct engine denial with Gateway up/down; original Gateway state restored'),
 ('N-05',1,900,'restore','Unapproved trust_remote_code true creates condition; remove clears; default zero conditions'),
 ('N-06',2,600,'restore','N+1 parallel streams; 429 Retry-After 1/CONCURRENCY_LIMIT; release/disconnect then 200'),
 ('R-11',2,900,'restore','AWS-07,08 passed; collectors, target and engine recover'),
 ('R-12',2,1200,'restore','AWS-13..17 passed; original bundle/collector/engine restored'),
 ('R-13',2,900,'continue','AWS-10,11,18,19 passed; phase and normal controls retained'),
 ('N-07',2,600,'restore','Actual stream idle_timeout/deadline_exceeded; exact settings restored'),
]
ROUND_MINUTES = {1:150, 2:120}
def describe(number):
    return [{'id':i,'timeout_seconds':t,'failure_rule':f,'criteria':c,'execution':'AWS에서 처음 실행됨'}
            for i,r,t,f,c in STEPS if r==number]
