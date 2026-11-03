; boot.s - the boot sectors of a2kit-boot.dsk: boot straight into the receiver.
;
; The Disk II boot ROM (16-sector, P5A) reads track 0 physical sector 0 to
; $0800, keeps reading physical sectors 1, 2, ... to $0900, $0A00, ... until
; it has as many as the byte at $0800 says, then jumps to $0801 with the
; controller's slot * 16 in $2B. This image asks for two sectors:
;
;   $0800       2               sector count for the boot ROM
;   $0801       JMP boot
;   $0804-      receiver.bin    (about 290 bytes) - moved down to $0800
;   $0980       restart         clear the screen, run the receiver; again after every session
;   $098E-      boot            set up, move the receiver into place, start
;
; The disk builder (tools/mkdisk.py) writes the second sector into every one of
; sectors 1-15 of track 0, so whichever of them the drive returns as physical
; sector 1 (DOS 3.3 and ProDOS orders disagree), the right bytes arrive.
;
; There is no DOS on the disk, so all of RAM stays free for a2kit.
;
; Build (needs receiver.bin): ca65 --bin-include-dir . runtime/boot.s -o boot.o
; or, for 115200 (needs receiver-115200.bin): add -D BAUD=115200 and write boot-115200.bin
;                             ld65 -C runtime/boot.cfg boot.o -o boot.bin

RECEIVER = $0800        ; where receiver.bin is assembled to run
SLOT16   = $2B          ; boot ROM: disk controller slot * 16
MOTOROFF = $C088        ; + slot * 16: stop the drive motor

INIT     = $FB2F        ; Monitor: text mode, full-screen window
HOME     = $FC58        ; Monitor: clear the screen
SETKBD   = $FE89        ; Monitor: input hook back to the keyboard
SETVID   = $FE93        ; Monitor: output hook back to the screen
MONZ     = $FF69        ; Monitor: command loop ("*" prompt), as CALL -151

        .segment "CODE"
        .org $0800

        .byte 2                 ; boot ROM: load physical sectors 0 and 1
        jmp boot

payload:
.ifndef BAUD
BAUD = 19200
.endif
.if BAUD = 19200
        .incbin "receiver.bin"
.else
        .incbin .sprintf("receiver-%d.bin", BAUD)
.endif
payload_end:
RXSIZE = payload_end - payload
        .assert RXSIZE > 256, error, "boot.s copies the receiver as one full page plus a tail"
        .assert payload_end <= restart, error, "receiver.bin is too big for the boot image"
        .assert RECEIVER + RXSIZE <= restart, error, "moved receiver would overwrite the restart loop"

        .res $0980 - *, $00

; $0980 (CALL 2432 from BASIC, 980G from the Monitor): reset the screen, run
; the receiver, and do both again each time a session ends, so there is always
; one fresh banner on a clean text screen. INIT also selects text mode, page 1
; and the full window, whatever the last program left switched on.
; The receiver returns with carry set only when a key was pressed while it was
; waiting; a2kit's own programs (the sequencer and the video player) return
; with carry clear.
restart:
        jsr INIT                ; text mode, page 1, full-screen window
        jsr HOME                ; clear it
        jsr RECEIVER
        bcc restart             ; a session ended: listen again
        jmp MONZ                ; a key while idle: go to the Monitor

boot:   cld
        ldx SLOT16
        lda MOTOROFF,x          ; the boot ROM leaves the drive running
        jsr SETVID              ; booting with PR#6 / IN#6 leaves the I/O hooks on
        jsr SETKBD              ; the disk card: COUT would reboot the disk
        ldy #0                  ; move the receiver down 4 bytes to $0800
@page:  lda payload,y           ; (copying upwards is safe: each byte is read
        sta RECEIVER,y          ;  before anything is written over it)
        iny
        bne @page
@tail:  lda payload+256,y
        sta RECEIVER+256,y
        iny
        cpy #<(RXSIZE - 256)
        bne @tail
        jmp restart

        .res $0A00 - *, $00
