On linux, need to make command:
stty -F /dev/ttyUSB0 -hupcl
to prevent controller reset after every start
