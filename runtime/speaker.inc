; ---------------------------------------------------------------------------
; Speaker PWM player. Everything in here is cycle counted: it must not cross
; a page boundary (a taken branch that does costs an extra cycle), so it gets
; its own aligned segment. Do not edit without re-running tools/test_sequencer.py.
;
; The includer defines: SPKR, aptr (2 bytes, low byte 0), apages, atmp, atmp2.
; Call: aptr = page-aligned data, apages = number of 256-byte pages, jsr splay.
; (splay, below play, can hop into a language card; play is main RAM or as-is.)
;
; Data: 4-bit samples, two per byte (high nibble first), page aligned.
; Each sample n (0-15) toggles the speaker, waits 5(n+1)-1 cycles, toggles
; again, then waits out the rest of a fixed 128-cycle sample period
; (7972 Hz at the Apple II's 1.020484 MHz average clock). Every instruction
; here takes the same number of cycles on the 6502 and the 65C02.
; ---------------------------------------------------------------------------
        .segment "PLAYER"

play:   ldy #0
@byte:
        ; ---- high nibble ----
        lda (aptr),y             ; 5
        sta atmp                 ; 3
        lsr a                   ; 2
        lsr a                   ; 2
        lsr a                   ; 2
        lsr a                   ; 2
        tax                     ; 2
        inx                     ; 2   X = n+1
        stx atmp2                ; 3
        bit SPKR                ; 4   edge 1
@d1:    dex                     ; 2
        bne @d1                 ; 3/2
        bit SPKR                ; 4   edge 2
        lda #17                 ; 2
        sec                     ; 2
        sbc atmp2                ; 3
        tax                     ; 2   X = 17-(n+1)
@d2:    dex
        bne @d2
        nop                     ; 8 nops = 16 cycles: equalise the two halves
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        ; ---- low nibble ----
        lda atmp                 ; 3
        and #$0F                ; 2
        tax                     ; 2
        inx                     ; 2
        stx atmp2                ; 3
        bit SPKR                ; edge 1
@d3:    dex
        bne @d3
        bit SPKR                ; edge 2
        lda #17
        sec
        sbc atmp2
        tax
@d4:    dex
        bne @d4
        iny                     ; 2
        bne @byte               ; 3 (2 at the end of each page)
        inc aptr+1
        dec apages
        bne @byte
        rts

; Play apages pages from aptr, wherever they are. Sound runs from main RAM up to
; $BFFF and carries on at $D000 in bank 2 of a language card, skipping the I/O
; at $C000-$CFFF. The card is only switched in around the pages that are in it,
; and ROM is back before this returns, so a machine with no card never sees the
; switches. The hop costs a few dozen cycles once, less than one 128-cycle sample.
LC_RAM  = $C080                 ; read card RAM, bank 2; no writes
LC_ROM  = $C082                 ; read ROM; no writes

splay:  lda aptr+1
        cmp #$C0
        bne @chk
        lda #$D0                ; the last burst stopped exactly at $C000
        sta aptr+1
@chk:   cmp #$C0
        bcs @card               ; already up in the card
        clc
        adc apages              ; A = the page it would end at
        cmp #$C1
        bcc play                ; all in main RAM
        sbc #$C0                ; (carry is set) A = pages past $C000
        pha
        eor #$FF
        sec
        adc apages              ; apages - that = pages below $C000
        sta apages
        jsr play
        lda #$D0
        sta aptr+1
        pla
        sta apages
@card:  bit LC_RAM
        jsr play
        bit LC_ROM
        rts
