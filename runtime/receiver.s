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
; Loads at $0800, staging buffer at $0A00. 8N1; slot and speed in config.inc.
; The banner shows the speed it was built for, so a mismatch is easy to spot.
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
COUT    = $FDED

; zero page the Apple II monitor and Applesoft leave free
ptr     = $06                   ; 2 bytes: destination address
cnt     = $08                   ; packet data length
sum     = $09                   ; running XOR checksum
tmo     = $EC                   ; 2 bytes: timeout counter / banner index

buf     = $0A00                 ; packet staging buffer (255 bytes max)

        .segment "CODE"

start:
        ; banner
        lda #0
        sta tmo
@ban:   ldy tmo
        lda msg,y
        beq @init
        ora #$80
        jsr COUT
        inc tmo
        bne @ban

@init:  ldx #SLOTX              ; X holds the slot offset from here on
        sta ACIA_STAT,x         ; programmed reset
        lda #$0B                ; no parity, RTS low, IRQs off, DTR on
        sta ACIA_CMD,x
        lda #ACIA_CTRL_VALUE    ; line speed, 8 bits, 1 stop (config.inc)
        sta ACIA_CTRL,x
        bit KBDSTRB             ; clear any pending keypress

packet:
        jsr getsync
        bcc @go
        rts                     ; key pressed: quit
@go:
        lda #0
        sta sum
        jsr rxb                 ; seq (not used, but part of the checksum)
        bcs packet
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
        jsr txb
        jmp packet
nak:    lda #PKT_NAK
        jsr txb
        jmp packet

; end marker received
fin:    lda #PKT_ACK
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

; wait for the $A5 sync byte; carry set if a key was pressed instead
getsync:
@wait:  lda KBD
        bmi @key
        lda ACIA_STAT,x
        and #$08
        beq @wait
        lda ACIA_DATA,x
        cmp #PKT_SYNC
        bne @wait
        clc
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

msg:    .byte .sprintf("A2KIT RX READY %d (ANY KEY QUITS)", BAUD), $0D, 0
