; a2kit block header (8 bytes, little-endian). Must match tools/a2hdr.py.

A2_FORMAT_VERSION = 1

; header field offsets
HDR_MAGIC0  = 0         ; 'A'
HDR_MAGIC1  = 1         ; '2'
HDR_VERSION = 2
HDR_TYPE    = 3
HDR_LEN_LO  = 4
HDR_LEN_HI  = 5
HDR_CYC_LO  = 6
HDR_CYC_HI  = 7
HDR_SIZE    = 8

; block types
TYPE_IMAGE_HGR  = 1
TYPE_IMAGE_DHGR = 2
TYPE_VIDEO      = 3
TYPE_AUDIO      = 4
TYPE_EFFECT     = 5

; serial packet protocol (see tools/send.py)
PKT_SYNC = $A5
PKT_READ = $A6
PKT_CRASH = $C3
PKT_ACK  = $06
PKT_NAK  = $15
