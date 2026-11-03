; ---------------------------------------------------------------------------
; Speaker PWM player. Everything in here is cycle counted: it must not cross
; a page boundary (a taken branch that does costs an extra cycle), so it gets
; its own aligned segment. Do not edit without re-running tools/test_sequencer.py.
;
; The includer defines: SPKR, aptr (2 bytes, low byte 0), apages, atmp, atmp2.
; Call: aptr = page-aligned data, apages = number of 256-byte pages, jsr play.
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
