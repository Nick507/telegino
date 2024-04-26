source ~/.venv/bin/activate
stty -F /dev/ttyUSB0 -hupcl
python3 telegino.py >> stdout.log &