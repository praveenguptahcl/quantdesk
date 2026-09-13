.PHONY: run test multiuser doctor
run:      ; ./run.sh
multiuser:; cd api && NQQ_MULTIUSER=1 python3 server.py
test:     ; python3 -m unittest discover -s tests -v
doctor:   ; cd api && python3 doctor.py
