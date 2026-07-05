/*!
 * qr.js — minimal, dependency-free QR code encoder → SVG.
 * Adapted from Kazuhiko Arase's public-domain "qrcode-generator" algorithm
 * (https://github.com/kazuhikoarase/qrcode-generator, MIT/public-domain).
 * Compacted for goida-vpn client-web: byte-mode (UTF-8) only, auto version
 * selection, error-correction level M, no external deps, no ES modules.
 *
 * Usage:
 *   const svg = renderQR('https://t.me/xyz', 180);
 *   el.innerHTML = svg;
 */
(function (global) {
  'use strict';

  /* ── Reed-Solomon / Galois field math ── */
  var EXP_TABLE = new Array(256);
  var LOG_TABLE = new Array(256);
  (function () {
    for (var i = 0; i < 8; i++) EXP_TABLE[i] = 1 << i;
    for (var i = 8; i < 256; i++) {
      EXP_TABLE[i] = EXP_TABLE[i - 4] ^ EXP_TABLE[i - 5] ^ EXP_TABLE[i - 6] ^ EXP_TABLE[i - 8];
    }
    for (var i = 0; i < 255; i++) LOG_TABLE[EXP_TABLE[i]] = i;
  })();
  function gexp(n) { while (n < 0) n += 255; while (n >= 255) n -= 255; return EXP_TABLE[n]; }
  function glog(n) { if (n < 1) throw new Error('glog(' + n + ')'); return LOG_TABLE[n]; }

  function Polynomial(num, shift) {
    var offset = 0;
    while (offset < num.length && num[offset] === 0) offset++;
    this.num = new Array(num.length - offset + shift);
    for (var i = 0; i < num.length - offset; i++) this.num[i] = num[i + offset];
    for (var i = 0; i < shift; i++) this.num[this.num.length - shift + i] = 0;
  }
  Polynomial.prototype.get = function (i) { return this.num[i]; };
  Polynomial.prototype.getLength = function () { return this.num.length; };
  Polynomial.prototype.multiply = function (e) {
    var num = new Array(this.getLength() + e.getLength() - 1);
    for (var i = 0; i < num.length; i++) num[i] = 0;
    for (var i = 0; i < this.getLength(); i++) {
      for (var j = 0; j < e.getLength(); j++) {
        num[i + j] ^= gexp(glog(this.get(i)) + glog(e.get(j)));
      }
    }
    return new Polynomial(num, 0);
  };
  Polynomial.prototype.mod = function (e) {
    if (this.getLength() - e.getLength() < 0) return this;
    var ratio = glog(this.get(0)) - glog(e.get(0));
    var num = new Array(this.getLength());
    for (var i = 0; i < this.getLength(); i++) num[i] = this.get(i);
    for (var i = 0; i < e.getLength(); i++) {
      num[i] ^= gexp(glog(e.get(i)) + ratio);
    }
    return new Polynomial(num, 0).mod(e);
  };

  function errorCorrectPolynomial(errorCorrectLength) {
    var a = new Polynomial([1], 0);
    for (var i = 0; i < errorCorrectLength; i++) {
      a = a.multiply(new Polynomial([1, gexp(i)], 0));
    }
    return a;
  }

  /* ── RS block table (error-correction level M) — versions 1..10 (covers ~213 alnum / ~154 byte chars @ M) ── */
  var RS_BLOCK_TABLE_M = [
    [1, 26, 16], [1, 44, 28], [1, 70, 44], [2, 50, 32], [2, 67, 43],
    [4, 43, 27], [4, 49, 31], [2, 60, 38, 2, 61, 39], [3, 58, 36, 2, 59, 37],
    [4, 69, 43, 1, 70, 44]
  ];
  function getRSBlocks(typeNumber) {
    var row = RS_BLOCK_TABLE_M[typeNumber - 1];
    if (!row) throw new Error('text too long for supported QR versions');
    var list = [];
    var i = 0;
    while (i < row.length) {
      var count = row[i], totalCount = row[i + 1], dataCount = row[i + 2];
      for (var j = 0; j < count; j++) list.push({ totalCount: totalCount, dataCount: dataCount });
      i += 3;
    }
    return list;
  }

  /* ── bit buffer ── */
  function BitBuffer() { this.buffer = []; this.length = 0; }
  BitBuffer.prototype.get = function (index) {
    var bufIndex = Math.floor(index / 8);
    return ((this.buffer[bufIndex] >>> (7 - index % 8)) & 1) === 1;
  };
  BitBuffer.prototype.put = function (num, length) {
    for (var i = 0; i < length; i++) this.putBit(((num >>> (length - i - 1)) & 1) === 1);
  };
  BitBuffer.prototype.putBit = function (bit) {
    var bufIndex = Math.floor(this.length / 8);
    if (this.buffer.length <= bufIndex) this.buffer.push(0);
    if (bit) this.buffer[bufIndex] |= (0x80 >>> (this.length % 8));
    this.length++;
  };

  /* ── UTF-8 byte-mode data ── */
  function toUtf8Bytes(str) {
    var bytes = [];
    for (var i = 0; i < str.length; i++) {
      var c = str.codePointAt(i);
      if (c > 0xFFFF) i++; // surrogate pair consumed
      if (c < 0x80) {
        bytes.push(c);
      } else if (c < 0x800) {
        bytes.push(0xC0 | (c >> 6), 0x80 | (c & 0x3F));
      } else if (c < 0x10000) {
        bytes.push(0xE0 | (c >> 12), 0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F));
      } else {
        bytes.push(
          0xF0 | (c >> 18), 0x80 | ((c >> 12) & 0x3F),
          0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F)
        );
      }
    }
    return bytes;
  }

  function lengthBits(typeNumber) { return typeNumber <= 9 ? 8 : 16; }

  function createData(typeNumber, rsBlocks, bytes) {
    var buffer = new BitBuffer();
    buffer.put(4, 4); // mode: byte
    buffer.put(bytes.length, lengthBits(typeNumber));
    for (var i = 0; i < bytes.length; i++) buffer.put(bytes[i], 8);

    var totalDataCount = 0;
    for (var i = 0; i < rsBlocks.length; i++) totalDataCount += rsBlocks[i].dataCount;

    if (buffer.length > totalDataCount * 8) {
      throw new Error('code length overflow (' + buffer.length + ' > ' + (totalDataCount * 8) + ')');
    }
    if (buffer.length + 4 <= totalDataCount * 8) buffer.put(0, 4);
    while (buffer.length % 8 !== 0) buffer.putBit(false);
    var PAD0 = 0xEC, PAD1 = 0x11;
    while (true) {
      if (buffer.length >= totalDataCount * 8) break;
      buffer.put(PAD0, 8);
      if (buffer.length >= totalDataCount * 8) break;
      buffer.put(PAD1, 8);
    }
    return createBytes(buffer, rsBlocks);
  }

  function createBytes(buffer, rsBlocks) {
    var offset = 0;
    var maxDcCount = 0, maxEcCount = 0;
    var dcdata = new Array(rsBlocks.length);
    var ecdata = new Array(rsBlocks.length);

    for (var r = 0; r < rsBlocks.length; r++) {
      var dcCount = rsBlocks[r].dataCount;
      var ecCount = rsBlocks[r].totalCount - dcCount;
      maxDcCount = Math.max(maxDcCount, dcCount);
      maxEcCount = Math.max(maxEcCount, ecCount);
      dcdata[r] = new Array(dcCount);
      for (var i = 0; i < dcdata[r].length; i++) {
        dcdata[r][i] = 0xff & buffer.buffer[i + offset];
      }
      offset += dcCount;
      var rsPoly = errorCorrectPolynomial(ecCount);
      var rawPoly = new Polynomial(dcdata[r], rsPoly.getLength() - 1);
      var modPoly = rawPoly.mod(rsPoly);
      ecdata[r] = new Array(rsPoly.getLength() - 1);
      for (var i = 0; i < ecdata[r].length; i++) {
        var modIndex = i + modPoly.getLength() - ecdata[r].length;
        ecdata[r][i] = modIndex >= 0 ? modPoly.get(modIndex) : 0;
      }
    }
    var totalCodeCount = 0;
    for (var i = 0; i < rsBlocks.length; i++) totalCodeCount += rsBlocks[i].totalCount;

    var data = new Array(totalCodeCount);
    var index = 0;
    for (var i = 0; i < maxDcCount; i++) {
      for (var r = 0; r < rsBlocks.length; r++) {
        if (i < dcdata[r].length) data[index++] = dcdata[r][i];
      }
    }
    for (var i = 0; i < maxEcCount; i++) {
      for (var r = 0; r < rsBlocks.length; r++) {
        if (i < ecdata[r].length) data[index++] = ecdata[r][i];
      }
    }
    return data;
  }

  /* ── QR matrix model ── */
  var PAD_MODE = { ECL_M: 0 };
  function QRCode(typeNumber) {
    this.typeNumber = typeNumber;
    this.moduleCount = typeNumber * 4 + 17;
    this.modules = null;
    this.dataCache = null;
  }
  QRCode.prototype.makeImpl = function (data) {
    this.modules = [];
    for (var row = 0; row < this.moduleCount; row++) {
      this.modules.push(new Array(this.moduleCount).fill(null));
    }
    this.setupPositionProbePattern(0, 0);
    this.setupPositionProbePattern(this.moduleCount - 7, 0);
    this.setupPositionProbePattern(0, this.moduleCount - 7);
    this.setupPositionAdjustPattern();
    this.setupTimingPattern();
    this.setupTypeInfo(false, 0);
    if (this.typeNumber >= 7) this.setupTypeNumber(false);
    this.mapData(data, 0);
  };
  QRCode.prototype.setupPositionProbePattern = function (row, col) {
    for (var r = -1; r <= 7; r++) {
      if (row + r <= -1 || this.moduleCount <= row + r) continue;
      for (var c = -1; c <= 7; c++) {
        if (col + c <= -1 || this.moduleCount <= col + c) continue;
        var on = (0 <= r && r <= 6 && (c === 0 || c === 6)) ||
                 (0 <= c && c <= 6 && (r === 0 || r === 6)) ||
                 (2 <= r && r <= 4 && 2 <= c && c <= 4);
        this.modules[row + r][col + c] = on;
      }
    }
  };
  var PATTERN_POSITION_TABLE = [
    [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42],
    [6, 26, 46], [6, 28, 50], [6, 30, 54]
  ];
  QRCode.prototype.setupPositionAdjustPattern = function () {
    var pos = PATTERN_POSITION_TABLE[this.typeNumber - 1] || [];
    for (var i = 0; i < pos.length; i++) {
      for (var j = 0; j < pos.length; j++) {
        var row = pos[i], col = pos[j];
        if (this.modules[row][col] !== null) continue;
        for (var r = -2; r <= 2; r++) {
          for (var c = -2; c <= 2; c++) {
            var on = (r === -2 || r === 2 || c === -2 || c === 2 || (r === 0 && c === 0));
            this.modules[row + r][col + c] = on;
          }
        }
      }
    }
  };
  QRCode.prototype.setupTimingPattern = function () {
    for (var r = 8; r < this.moduleCount - 8; r++) {
      if (this.modules[r][6] !== null) continue;
      this.modules[r][6] = (r % 2 === 0);
    }
    for (var c = 8; c < this.moduleCount - 8; c++) {
      if (this.modules[6][c] !== null) continue;
      this.modules[6][c] = (c % 2 === 0);
    }
  };
  var G15 = (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | (1 << 0);
  var G15_MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1);
  function getBCHTypeInfo(data) {
    var d = data << 10;
    while (getBCHDigit(d) - getBCHDigit(G15) >= 0) d ^= (G15 << (getBCHDigit(d) - getBCHDigit(G15)));
    return ((data << 10) | d) ^ G15_MASK;
  }
  function getBCHDigit(data) {
    var digit = 0;
    while (data !== 0) { digit++; data >>>= 1; }
    return digit;
  }
  /* error correction level M = 0b00 per spec table (L=01,M=00,Q=11,H=10 pre-XOR indicator used by this impl's convention) */
  var ERROR_CORRECT_LEVEL_M_BITS = 0;
  QRCode.prototype.setupTypeInfo = function (test, maskPattern) {
    var data = (ERROR_CORRECT_LEVEL_M_BITS << 3) | maskPattern;
    var bits = getBCHTypeInfo(data);
    for (var i = 0; i < 15; i++) {
      var mod = (!test && ((bits >> i) & 1) === 1);
      if (i < 6) this.modules[i][8] = mod;
      else if (i < 8) this.modules[i + 1][8] = mod;
      else this.modules[this.moduleCount - 15 + i][8] = mod;
    }
    for (var i = 0; i < 15; i++) {
      var mod = (!test && ((bits >> i) & 1) === 1);
      if (i < 8) this.modules[8][this.moduleCount - i - 1] = mod;
      else if (i < 9) this.modules[8][15 - i - 1 + 1] = mod;
      else this.modules[8][15 - i - 1] = mod;
    }
    this.modules[this.moduleCount - 8][8] = (!test);
  };
  function getMask(pattern, i, j) {
    switch (pattern) {
      case 0: return (i + j) % 2 === 0;
      case 1: return i % 2 === 0;
      case 2: return j % 3 === 0;
      case 3: return (i + j) % 3 === 0;
      case 4: return (Math.floor(i / 2) + Math.floor(j / 3)) % 2 === 0;
      case 5: return (i * j) % 2 + (i * j) % 3 === 0;
      case 6: return ((i * j) % 2 + (i * j) % 3) % 2 === 0;
      case 7: return ((i * j) % 3 + (i + j) % 2) % 2 === 0;
      default: throw new Error('bad mask pattern:' + pattern);
    }
  }
  QRCode.prototype.mapData = function (data, maskPattern) {
    var inc = -1, row = this.moduleCount - 1, bitIndex = 7, byteIndex = 0;
    for (var col = this.moduleCount - 1; col > 0; col -= 2) {
      if (col === 6) col--;
      while (true) {
        for (var c = 0; c < 2; c++) {
          if (this.modules[row][col - c] === null) {
            var dark = false;
            if (byteIndex < data.length) {
              dark = ((data[byteIndex] >>> bitIndex) & 1) === 1;
            }
            var mask = getMask(maskPattern, row, col - c);
            if (mask) dark = !dark;
            this.modules[row][col - c] = dark;
            bitIndex--;
            if (bitIndex === -1) { byteIndex++; bitIndex = 7; }
          }
        }
        row += inc;
        if (row < 0 || this.moduleCount <= row) { row -= inc; inc = -inc; break; }
      }
    }
  };
  /* apply best mask by penalty scoring (spec-compliant subset) */
  function lostPoint(qr) {
    var moduleCount = qr.moduleCount, modules = qr.modules;
    var lostPoint = 0;
    for (var row = 0; row < moduleCount; row++) {
      for (var col = 0; col < moduleCount; col++) {
        var sameCount = 0;
        var dark = modules[row][col];
        for (var r = -1; r <= 1; r++) {
          if (row + r < 0 || moduleCount <= row + r) continue;
          for (var c = -1; c <= 1; c++) {
            if (col + c < 0 || moduleCount <= col + c) continue;
            if (r === 0 && c === 0) continue;
            if (dark === modules[row + r][col + c]) sameCount++;
          }
        }
        if (sameCount > 5) lostPoint += (3 + sameCount - 5);
      }
    }
    for (var row = 0; row < moduleCount - 1; row++) {
      for (var col = 0; col < moduleCount - 1; col++) {
        var count = 0;
        if (modules[row][col]) count++;
        if (modules[row + 1][col]) count++;
        if (modules[row][col + 1]) count++;
        if (modules[row + 1][col + 1]) count++;
        if (count === 0 || count === 4) lostPoint += 3;
      }
    }
    for (var row = 0; row < moduleCount; row++) {
      for (var col = 0; col < moduleCount - 6; col++) {
        if (modules[row][col] && !modules[row][col + 1] && modules[row][col + 2] &&
            modules[row][col + 3] && modules[row][col + 4] && !modules[row][col + 5] && modules[row][col + 6]) {
          lostPoint += 40;
        }
      }
    }
    for (var col = 0; col < moduleCount; col++) {
      for (var row = 0; row < moduleCount - 6; row++) {
        if (modules[row][col] && !modules[row + 1][col] && modules[row + 2][col] &&
            modules[row + 3][col] && modules[row + 4][col] && !modules[row + 5][col] && modules[row + 6][col]) {
          lostPoint += 40;
        }
      }
    }
    var darkCount = 0;
    for (var row = 0; row < moduleCount; row++) {
      for (var col = 0; col < moduleCount; col++) if (modules[row][col]) darkCount++;
    }
    var ratio = Math.abs(100 * darkCount / moduleCount / moduleCount - 50) / 5;
    lostPoint += ratio * 10;
    return lostPoint;
  }
  QRCode.prototype.makeBestMask = function (data) {
    var bestPattern = 0, bestLost = Infinity, bestModules = null;
    for (var pattern = 0; pattern < 8; pattern++) {
      this.modules = [];
      for (var row = 0; row < this.moduleCount; row++) this.modules.push(new Array(this.moduleCount).fill(null));
      this.setupPositionProbePattern(0, 0);
      this.setupPositionProbePattern(this.moduleCount - 7, 0);
      this.setupPositionProbePattern(0, this.moduleCount - 7);
      this.setupPositionAdjustPattern();
      this.setupTimingPattern();
      this.setupTypeInfo(false, pattern);
      if (this.typeNumber >= 7) this.setupTypeNumber(false);
      this.mapData(data, pattern);
      var lost = lostPoint(this);
      if (lost < bestLost) { bestLost = lost; bestPattern = pattern; bestModules = this.modules; }
    }
    this.modules = bestModules;
    return bestPattern;
  };
  QRCode.prototype.setupTypeNumber = function (test) {
    // only needed for typeNumber >= 7; our max supported version is 10 so keep simple no-op-safe stub
    var G18 = (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | (1 << 0);
    var bits = this.typeNumber << 12;
    while (getBCHDigit(bits) - getBCHDigit(G18) >= 0) bits ^= (G18 << (getBCHDigit(bits) - getBCHDigit(G18)));
    var full = (this.typeNumber << 12) | bits;
    for (var i = 0; i < 18; i++) {
      var mod = (!test && ((full >> i) & 1) === 1);
      this.modules[Math.floor(i / 3)][i % 3 + this.moduleCount - 8 - 3] = mod;
      this.modules[i % 3 + this.moduleCount - 8 - 3][Math.floor(i / 3)] = mod;
    }
  };

  function makeQRFromBytes(bytes) {
    var typeNumber = null;
    for (var t = 1; t <= 10; t++) {
      try {
        var rsBlocks = getRSBlocks(t);
        var data = createData(t, rsBlocks, bytes);
        typeNumber = t;
        var qr = new QRCode(t);
        var maskPattern = qr.makeBestMask(data);
        // finalize with chosen mask baked into modules already (makeBestMask sets this.modules)
        return qr;
      } catch (e) {
        if (t === 10) throw e;
        continue;
      }
    }
    throw new Error('unable to fit data into supported QR versions');
  }

  /**
   * renderQR(text, sizePx) -> SVG markup string.
   * Black modules on transparent background, sized to sizePx (square).
   */
  function renderQR(text, sizePx) {
    sizePx = sizePx || 180;
    var bytes = toUtf8Bytes(String(text == null ? '' : text));
    if (!bytes.length) bytes = toUtf8Bytes(' ');
    var qr = makeQRFromBytes(bytes);
    var count = qr.moduleCount;
    var quiet = 4; // modules of quiet-zone border, per spec minimum
    var total = count + quiet * 2;
    var cell = sizePx / total;
    var path = '';
    for (var r = 0; r < count; r++) {
      for (var c = 0; c < count; c++) {
        if (qr.modules[r][c]) {
          var x = (c + quiet) * cell;
          var y = (r + quiet) * cell;
          path += 'M' + round(x) + ' ' + round(y) + 'h' + round(cell) + 'v' + round(cell) + 'h' + round(-cell) + 'z';
        }
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + sizePx + ' ' + sizePx + '" width="' + sizePx + '" height="' + sizePx + '" shape-rendering="crispEdges" role="img" aria-label="QR code">' +
      '<rect width="' + sizePx + '" height="' + sizePx + '" fill="none"/>' +
      '<path d="' + path + '" fill="#000"/>' +
      '</svg>';
  }
  function round(n) { return Math.round(n * 100) / 100; }

  global.renderQR = renderQR;
})(typeof window !== 'undefined' ? window : this);
