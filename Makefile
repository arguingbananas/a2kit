all: receiver.bin sequencer.bin video.bin boot.bin a2kit-boot.dsk

receiver.bin: runtime/receiver.s runtime/hdr.inc runtime/config.inc runtime/receiver.cfg
	ca65 -I runtime runtime/receiver.s -o receiver.o
	ld65 -C runtime/receiver.cfg receiver.o -o receiver.bin

sequencer.bin: runtime/sequencer.s runtime/sequencer.cfg runtime/speaker.inc
	ca65 -I runtime runtime/sequencer.s -o sequencer.o
	ld65 -C runtime/sequencer.cfg sequencer.o -o sequencer.bin

video.bin: runtime/video.s runtime/video.cfg runtime/speaker.inc runtime/config.inc
	ca65 -I runtime runtime/video.s -o video.o
	ld65 -C runtime/video.cfg video.o -o video.bin -Ln video.lbl

boot.bin: runtime/boot.s runtime/boot.cfg receiver.bin
	ca65 --bin-include-dir . runtime/boot.s -o boot.o
	ld65 -C runtime/boot.cfg boot.o -o boot.bin

a2kit-boot.dsk: boot.bin tools/mkdisk.py
	python3 tools/mkdisk.py -o a2kit-boot.dsk

test: all
	python3 tools/test_a2kit.py
	python3 tools/test_receiver.py receiver.bin
	python3 tools/test_sequencer.py
	python3 tools/test_video.py
	python3 tools/test_boot.py
	python3 tools/test_ping.py
	python3 tools/test_cli.py
	python3 tools/test_examples.py

clean:
	rm -f *.o receiver.bin sequencer.bin video.bin video.lbl boot.bin a2kit-boot.dsk
