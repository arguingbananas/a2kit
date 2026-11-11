; receiver.s - a2kit serial receiver for the Super Serial Card (6551 ACIA)
;
; Speaks the packet protocol in tools/send.py:
;   $A5 seq len addr_lo addr_hi data[len] checksum      (checksum = XOR)
; Replies $06 (ACK) or $15 (NAK) per packet.
;
; Packets are staged in a buffer and only copied to their destination after
; the checksum passes, so a corrupted address can't scribble over memory.
;
; End marker (len = 0): addr <> 0 -> ACK, then JSR addr
;                       addr  = 0 -> ACK, show HGR page 1, wait for a key
; Press any key while idle to quit.
;
; Read request: $A6 count 0 addr_lo addr_hi checksum (count 0 = 256 bytes).
; Replies ACK, the bytes, then their XOR; or NAK if the checksum is bad.
;
; Crash report: the receiver points the Monitor's BRK vector at "crash", which
; sends $C3, sets the crash flag, prints the registers and keeps listening, so
; the PC can read what the Monitor saved. A key still drops to the Monitor.
; The IRQ vector points at the Monitor's own BRK path, so a stray interrupt is
; reported the same way instead of jumping through power-up junk; its saved PC
; is then the interrupted address itself, not 2 past a BRK.
;
; With a language card in slot 0, $D000-$FFFF writes land in its RAM (bank 2).
;
; Loads at $0800, staging buffer at $0A00. 8N1; slot and speed in config.inc.
; The banner is just A2KIT: the computer reads this code back on first contact
; and writes which build and speed it found to the bottom line (tools/rxid.py).
;
; At 115200 baud a byte arrives every 88.6 cycles and the 6551 holds only one,
; so each byte must be read before the next one finishes arriving. rxb and the
; loops that call it are kept short enough for that (tools/test_speed.py checks
; it cycle by cycle).
; Build:  ca65 -I runtime [-D BAUD=115200] runtime/receiver.s -o receiver.o
;         ld65 -C runtime/receiver.cfg receiver.o -o receiver.bin

        .include "hdr.inc"
        .include "config.inc"           ; SLOT and baud rate

ACIA_DATA = $C088               ; + SLOTX
ACIA_STAT = $C089
ACIA_CMD  = $C08A
ACIA_CTRL = $C08B

KBD     = $C000
KBDSTRB = $C010
TXTCLR  = $C050                 ; graphics on
TXTSET  = $C051                 ; text on
MIXCLR  = $C052                 ; full screen
LOWSCR  = $C054                 ; page 1
HIRES   = $C057
LC_WRITE = $C081                ; read twice: read ROM, write a language card's RAM (bank 2)
COUT    = $FDED
BRKVEC  = $03F0                 ; Monitor: where BRK goes after saving the registers
IRQVEC  = $03FE                 ; Monitor: where an IRQ goes (power-up junk unless set)
MONBRK  = $FA4C                 ; Monitor: its BRK path (save the registers, JMP (BRKVEC))
INSDS1  = $F882                 ; Monitor: print the saved PC
RGDSP1  = $FADA                 ; Monitor: print the saved registers
MON     = $FF65                 ; Monitor: command loop

; zero page the Apple II monitor and Applesoft leave free
ptr     = $06                   ; 2 bytes: destination address
cnt     = $08                   ; packet data length
sum     = $09                   ; running XOR checksum
crashed = $CE                   ; $C3 after a crash, 0 from each normal start (tools/send.py)
tmo     = $EC                   ; 2 bytes: timeout counter / banner index
kind    = $EE                   ; sync byte of the current packet
seq     = $EF                   ; seq byte of the current packet: the count, in a read request

buf     = $0A00                 ; packet staging buffer (255 bytes max)
CRASH_S = $20                   ; the crash handler's stack pointer

        .segment "CODE"

start:
        sei                     ; nothing here uses interrupts (init still points the
                                ; IRQ vector at the crash path, in case one gets through)
        ; banner
        lda #0
        sta crashed
        sta tmo
@ban:   ldy tmo
        lda msg,y
        beq @rdy
        ora #$80
        jsr COUT
        inc tmo
        bne @ban
@rdy:   jsr init

packet:
        jsr getsync
        bcc @go
        rts                     ; key pressed: quit
@go:
        sta kind
        lda #0
        sta sum
        jsr rxb                 ; seq (only a read request uses it)
        bcs packet
        sta seq
        jsr rxb                 ; len
        bcs packet
        sta cnt
        jsr rxb                 ; addr lo
        bcs packet
        sta ptr
        jsr rxb                 ; addr hi
        bcs packet
        sta ptr+1
        lda cnt
        beq getck               ; end marker has no data
        ldy #0
datalp: jsr rxb
        bcs packet
        sta buf,y
        iny
        cpy cnt
        bne datalp
getck:  jsr rxb                 ; checksum byte folds in; sum must end at 0
        bcs packet
        lda sum
        bne nak
        lda cnt
        beq fin
        ldy #0                  ; checksum good: copy to destination
cplp:   lda buf,y
        sta (ptr),y
        iny
        cpy cnt
        bne cplp
        lda #PKT_ACK
        bne reply
nak:    lda #PKT_NAK
reply:  jsr txb
        jmp packet

; end marker received
fin:    lda kind
        cmp #PKT_READ
        beq rdmem
        lda #PKT_ACK
        jsr txb
        ldy #0                  ; ~1 ms so the ACK finishes leaving the UART
@dly:   dey
        bne @dly
        lda ptr
        ora ptr+1
        beq show
        jsr runit
        rts
runit:  jmp (ptr)

show:   bit TXTCLR
        bit MIXCLR
        bit LOWSCR
        bit HIRES
@key:   lda KBD
        bpl @key
        bit KBDSTRB
        bit TXTSET
quit:   rts

; read request: ACK, seq bytes from ptr (0 = 256), their XOR (sum is 0 here)
rdmem:  lda #PKT_ACK
        jsr txb
        ldy #0
@lp:    lda (ptr),y
        jsr txb
        eor sum
        sta sum
        iny
        cpy seq
        bne @lp
        lda sum
        jmp reply

; BRK arrives here with the registers saved by the Monitor. The marker goes
; first and doesn't wait for the 6551, so neither a damaged screen hook nor a
; dead line can stop the rest. This never returns to the crashed program. It
; moves to its own part of the stack page, so its calls don't overwrite what the
; crashed program left near S (the Monitor has saved S itself).
crash:  sei                     ; the Monitor's PLP brought back the crashed I flag
        ldx #CRASH_S
        txs
        jsr init
        lda #PKT_CRASH
        sta crashed
        sta ACIA_DATA,x
        jsr INSDS1
        jsr RGDSP1
        ldx #SLOTX
        jsr packet
        jmp MON

init:   ldx #SLOTX              ; X holds the slot offset from here on
        sta ACIA_STAT,x         ; programmed reset
        lda #$0B                ; no parity, RTS low, IRQs off, DTR on
        sta ACIA_CMD,x
        lda #ACIA_CTRL_VALUE    ; line speed, 8 bits, 1 stop (config.inc)
        sta ACIA_CTRL,x
        lda #<crash
        sta BRKVEC
        lda #>crash
        sta BRKVEC+1
        lda #<MONBRK            ; a stray IRQ: treat it like a BRK, so it is reported
        sta IRQVEC
        lda #>MONBRK
        sta IRQVEC+1
        bit LC_WRITE            ; packets for $D000 and up go to a language card; ROM
        bit LC_WRITE            ; stays readable, and with no card these do nothing
        bit KBDSTRB             ; clear any pending keypress
        rts

; wait for the $A5 sync byte; carry set if a key was pressed instead
getsync:
@wait:  lda KBD
        bmi @key
        lda ACIA_STAT,x
        and #$08
        beq @wait
        lda ACIA_DATA,x
        cmp #PKT_SYNC
        beq @sync
        cmp #PKT_READ
        bne @wait
@sync:  clc
        rts
@key:   bit KBDSTRB
        sec
        rts

; receive one byte with ~1 second timeout, fold into checksum.
; returns A = byte, carry clear; or carry set on timeout.
rxb:    lda #0
        sta tmo
        sta tmo+1
@poll:  lda ACIA_STAT,x
        and #$08
        bne @got
        inc tmo
        bne @poll
        inc tmo+1
        bne @poll
        sec
        rts
@got:   lda ACIA_DATA,x
        pha
        eor sum
        sta sum
        pla
        clc
        rts

; transmit A
txb:    pha
@wait:  lda ACIA_STAT,x
        and #$10
        beq @wait
        pla
        sta ACIA_DATA,x
        rts

msg:    .byte "A2KIT", $0D, 0         ; the speed and build: see tools/rxid.py
