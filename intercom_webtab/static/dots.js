// Dot-matrix weather icons: a 22x18 grid with a 3 px pitch (66x54). On a 1x iPad screen every dot
// lands exactly on 2x2 pixels with a 1 px gap, so nothing is blurred. Shapes are defined by geometry
// (circles, segments) and rasterized at the dot centers; an icon is built back to front from the
// "brushes" below. One frame is 150 ms, t grows forever (modulo FRAMES). ES5 only (iOS 9).
var DOTS = (function () {
  var C = 22, R = 18, P = 3;
  var OFF = "rgba(255,255,255,.10)", W = "#FFFFFF", GREY = "#8F9999", SUN = "#F2D37A", MOON = "#E6ECEC";
  var cW = "255,255,255", cSUN = "242,211,122", cMOON = "230,236,236", cDROP = "159,211,255",
      cICE = "210,244,255", cDUST = "217,165,94", cFOG = "216,222,222", cHOT = "242,153,74";
  function a(rgb, k) { return "rgba(" + rgb + "," + k + ")"; }

  // ---------- geometry ----------
  function circ(x, y, cx, cy, r) { var dx = x - cx, dy = y - cy; return dx * dx + dy * dy <= r * r; }
  function seg(x, y, ax, ay, bx, by, w) {                        // point within w of a segment
    var vx = bx - ax, vy = by - ay, k = ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy);
    k = k < 0 ? 0 : k > 1 ? 1 : k;
    var dx = ax + k * vx - x, dy = ay + k * vy - y;
    return dx * dx + dy * dy <= w * w;
  }
  // cloud 20x10 at k=1: big dome on the left, small one on the right, capsule base; (ox, oy) = top left
  function cloud(x, y, ox, oy, k) {
    x = (x - ox) / k; y = (y - oy) / k;
    if (y > 9.6) return false;
    return circ(x, y, 6.2, 5.2, 4.4) || circ(x, y, 13.6, 6.0, 3.6) || seg(x, y, 3.0, 7.8, 17.4, 7.8, 1.9);
  }
  function tri(t, half) { var m = t % (2 * half); return m < half ? m : 2 * half - m; }   // 0..half..0

  // ---------- brushes ----------
  function blank() { var g = [], x, y; for (y = 0; y < R; y++) { g[y] = []; for (x = 0; x < C; x++) g[y][x] = OFF; } return g; }
  function put(g, y, x, col) { if (y >= 0 && y < R && x >= 0 && x < C) g[y][x] = col; }
  function shape(g, test, col, halo) {                            // halo: one-dot gap around the shape
    var x, y;
    if (halo) for (y = 0; y < R; y++) for (x = 0; x < C; x++)
      if (!test(x, y) && (test(x - 1, y) || test(x + 1, y) || test(x, y - 1) || test(x, y + 1))) g[y][x] = OFF;
    for (y = 0; y < R; y++) for (x = 0; x < C; x++) if (test(x, y)) g[y][x] = col;
  }
  function cl(ox, oy, k) { return function (x, y) { return cloud(x, y, ox, oy, k); }; }
  function ray(ax, ay, bx, by) { return function (x, y) { return seg(x, y, ax, ay, bx, by, 0.55); }; }

  function sun(g, cx, cy, r, r1, r2, t, dim) {                    // disc + 8 rays, a glint runs around the rays
    shape(g, function (x, y) { return circ(x, y, cx, cy, r); }, dim ? a(cSUN, dim) : SUN);
    if (!r2) return;
    for (var i = 0; i < 8; i++) {
      var an = i * Math.PI / 4, s = Math.sin(an), c = Math.cos(an), q = (i - (t >> 1) + 800) % 8;
      shape(g, ray(cx + r1 * s, cy - r1 * c, cx + r2 * s, cy - r2 * c), q === 0 ? SUN : q === 1 || q === 7 ? a(cSUN, .7) : a(cSUN, .38));
    }
  }
  function moon(g, cx, cy, rr) {                                  // crescent: a circle minus a shifted circle
    shape(g, function (x, y) { return circ(x, y, cx, cy, rr) && !circ(x, y, cx + .54 * rr, cy - .34 * rr, .84 * rr); }, MOON);
  }
  function tint(g, rgb, k) {                                     // tint the "air": the background dots
    for (var y = 0; y < R; y++) for (var x = 0; x < C; x++) if (g[y][x] === OFF) g[y][x] = a(rgb, k);
  }
  function spark(g, y, x, rgb, k) {                               // five-dot cross
    put(g, y, x, a(rgb, 1)); put(g, y - 1, x, a(rgb, k)); put(g, y + 1, x, a(rgb, k)); put(g, y, x - 1, a(rgb, k)); put(g, y, x + 1, a(rgb, k));
  }
  function stars(g, list, t) {                                    // stars twinkle in turn
    for (var i = 0; i < list.length; i++) {
      var y = list[i][0], x = list[i][1], ph = ((t >> 2) + i) % 3;
      if (ph === 1) spark(g, y, x, cMOON, .5); else put(g, y, x, ph ? MOON : a(cMOON, .35));
    }
  }
  // drops: list [[column, phase]] fall from y0 down; div shifts t (0 = one row per frame); slant = one dot left per 2 rows
  function drops(g, list, y0, t, rgb, tail, div, slant) {
    var span = R - y0 + tail + 1;
    for (var i = 0; i < list.length; i++) {
      var r = y0 + ((t >> div) + list[i][1]) % span;
      for (var k = 0; k <= tail; k++) {
        var yy = r - k; if (yy < y0) break;
        put(g, yy, list[i][0] - (slant ? (yy - y0) >> 1 : 0), a(rgb, k === 0 ? 1 : k === 1 ? .5 : .22));
      }
    }
  }
  var SWAY = [0, 0, 1, 1, 0, 0, -1, -1];
  function flakes(g, list, y0, t, div, cross) {                   // snowflakes: a cross or a dot, swaying
    var span = R - y0 + 1;
    for (var i = 0; i < list.length; i++) {
      var s = ((t >> div) + list[i][1]) % span, r = y0 + s, c = list[i][0] + SWAY[s % 8];
      if (cross) spark(g, r, c, cW, .45); else put(g, r, c, W);
    }
  }
  function hail(g, list, y0, t) {                                 // 2x2 hailstones: 2 rows per frame, bounce at the ground
    for (var i = 0; i < list.length; i++) {
      var s = (t + list[i][1]) % 12, x = list[i][0], y = s < 3 ? y0 + s * 2 : s === 3 ? 16 : s === 4 ? 14 : s === 5 ? 16 : -9;
      put(g, y, x, W); put(g, y, x + 1, W); put(g, y + 1, x, W); put(g, y + 1, x + 1, W);
    }
  }
  function iceRow(g, y, t) {                                      // ice crust with a running glint
    var b = (t % 30) - 4;
    for (var x = 0; x < C; x++) put(g, y, x, Math.abs(x - b) < 1.5 ? W : a(cICE, .6));
  }
  function bolt(g, ox, t) {                                       // zigzag lightning, a double flash every 3 s
    var m = t % 20, fl = m === 0 || m === 2;
    if (m > 3) return;
    shape(g, function (x, y) {
      return y >= 10 && (seg(x, y, ox + 3, 10, ox, 14, 0.5) || seg(x, y, ox, 14, ox + 4, 14, 0.5) || seg(x, y, ox + 4, 14, ox + 1, 18, 0.5));
    }, fl ? SUN : a(cSUN, .3));
  }
  function flash(t) { var m = t % 20; return m === 0 || m === 2; }
  function lines(g, rows, t, rgb, gap, speed) {                   // horizontal bands with moving gaps
    for (var i = 0; i < rows.length; i++) for (var x = rows[i][1]; x <= rows[i][2]; x++) {
      var s = i % 2 ? (x + (t >> speed)) % gap : (x - (t >> speed) + 8000) % gap;
      put(g, rows[i][0], x, a(rgb, s < 2 ? .25 : 1));
    }
  }
  var WIND = [[[1, 5], [14, 5], [17, 4], [18, 2], [16, 1], [14, 2]],
              [[3, 9], [19, 9], [21, 10.5], [20.5, 12.5], [18.5, 12.5]],
              [[0, 13], [11, 13], [13.5, 14], [13.5, 16], [11.5, 16.2]]];
  function streams(g, t, rgb, base, only) {                     // wind: streams with curls, a stroke runs along them
    base = base || .6;
    for (var i = 0; i < WIND.length; i++) {
      if (only && only.indexOf(i) < 0) continue;
      var p = WIND[i], L = 0, k, at = [];
      for (k = 1; k < p.length; k++) { at.push(L); L += Math.sqrt(Math.pow(p[k][0] - p[k - 1][0], 2) + Math.pow(p[k][1] - p[k - 1][1], 2)); }
      var head = ((t * 0.9 + i * 7) % (L + 8)) - 4;
      for (var y = 0; y < R; y++) for (var x = 0; x < C; x++) for (k = 1; k < p.length; k++)
        if (seg(x, y, p[k - 1][0], p[k - 1][1], p[k][0], p[k][1], 0.62)) {
          var vx = p[k][0] - p[k - 1][0], vy = p[k][1] - p[k - 1][1], q = ((x - p[k - 1][0]) * vx + (y - p[k - 1][1]) * vy) / (vx * vx + vy * vy);
          var pos = at[k - 1] + Math.max(0, Math.min(1, q)) * Math.sqrt(vx * vx + vy * vy);
          g[y][x] = a(rgb, Math.abs(pos - head) < 2.5 ? 1 : base); break;
        }
    }
  }
  function crystal(g, cx, cy, rr, t) {                            // six-armed snowflake with branches, tips twinkle
    for (var i = 0; i < 6; i++) {
      var an = i * Math.PI / 3 + Math.PI / 2, s = Math.cos(an), c = Math.sin(an);
      var ex = cx + rr * s, ey = cy - rr * c, mx = cx + rr * .55 * s, my = cy - rr * .55 * c, bl = rr * .32;
      shape(g, ray(cx, cy, ex, ey), W);
      shape(g, ray(mx, my, mx + bl * Math.cos(an + Math.PI / 4), my - bl * Math.sin(an + Math.PI / 4)), a(cW, .75));
      shape(g, ray(mx, my, mx + bl * Math.cos(an - Math.PI / 4), my - bl * Math.sin(an - Math.PI / 4)), a(cW, .75));
      if (((t >> 2) + i) % 6 === 0) spark(g, Math.round(ey), Math.round(ex), cICE, .6);
    }
  }

  // ---------- scenes ----------
  var TOP = cl(0.5, 0, 1);
  function peek(g, t, night) {                                    // sun or moon peeking from behind a cloud
    if (night) { moon(g, 6.5, 5.2, 4.2); stars(g, [[1, 14]], t); }
    else sun(g, 6.5, 5, 2.7, 4.0, 5.4, t);
    shape(g, cl(4.4 + tri(t >> 4, 1), 2.4, 0.84), W, true);
  }
  var RAIN = [[2, 0], [5, 5], [8, 2], [11, 7], [14, 3], [17, 6], [20, 1]];
  var RAIN_SH = [[7, 0], [10, 5], [13, 2], [16, 7], [19, 3]];
  var HEAVY = [[2, 0], [4, 6], [6, 3], [8, 8], [10, 1], [12, 5], [14, 2], [16, 7], [18, 4], [20, 0], [22, 6], [24, 3]];
  var SNOW = [[3, 0], [8, 9], [13, 4], [18, 13], [6, 15], [16, 7]];
  var SNOW_H = [[2, 0], [5, 11], [8, 4], [11, 14], [14, 7], [17, 2], [20, 10], [4, 6], [12, 1], [18, 15]];
  var SNOW_SH = [[8, 0], [12, 9], [16, 4], [19, 13]];
  var FOG = [[1, 3, 19], [4, 0, 21], [7, 2, 20], [10, 1, 18], [13, 3, 21], [16, 0, 17]];

  var K = {
    "clear-day": function (g, t) { sun(g, 10.5, 8.5, 4.3, 6.2, 8.3, t); },
    "clear-night": function (g, t) { moon(g, 9.5, 9, 7.6); stars(g, [[3, 17], [9, 19], [14, 15]], t); },
    "mostly-clear-day": function (g, t) { sun(g, 9, 7, 3.8, 5.5, 7.3, t); shape(g, cl(9.5 + tri(t >> 3, 2), 10.6, 0.56), W, true); },
    "mostly-clear-night": function (g, t) { moon(g, 8.5, 8, 6.6); stars(g, [[2, 16], [7, 19]], t); shape(g, cl(9.5 + tri(t >> 3, 2), 10.6, 0.56), W, true); },
    "partly-cloudy-day": function (g, t) { sun(g, 7.5, 6.5, 3.3, 4.8, 6.4, t); shape(g, cl(4.5 + tri(t >> 3, 2), 6.6, 0.8), W, true); },
    "partly-cloudy-night": function (g, t) { moon(g, 7.5, 6.8, 5.6); stars(g, [[1, 15], [4, 20]], t); shape(g, cl(4.5 + tri(t >> 3, 2), 6.6, 0.8), W, true); },
    "overcast": function (g, t) {                                 // big cloud behind, small one in front, drifting past each other
      shape(g, cl(3.5 - tri(t >> 3, 2), 0.4, 0.92), GREY);
      shape(g, cl(tri(t >> 2, 6) - 1.5, 6.6, 0.72), W, true);
    },
    "mostly-cloudy-day": function (g, t) {                         // mostly cloudy: two clouds, the sun peeks out
      sun(g, 5.5, 4.5, 2.6, 3.8, 5.0, t);
      shape(g, cl(5.5 - tri(t >> 3, 1), 0.6, 0.8), GREY, true);
      shape(g, cl(tri(t >> 2, 5) - 1, 7.2, 0.68), W, true);
    },
    "mostly-cloudy-night": function (g, t) {
      moon(g, 5.5, 4.8, 4.0); stars(g, [[1, 20]], t);
      shape(g, cl(5.5 - tri(t >> 3, 1), 0.6, 0.8), GREY, true);
      shape(g, cl(tri(t >> 2, 5) - 1, 7.2, 0.68), W, true);
    },
    "fog": function (g, t) { lines(g, FOG, t, cFOG, 8, 1); },
    "rime-fog": function (g, t) {                                 // rime: fog plus settling ice sparks
      lines(g, FOG, t, cFOG, 8, 1);
      var S = [[2, 6], [5, 15], [8, 9], [11, 18], [14, 4], [3, 19], [9, 2], [15, 12]];
      for (var i = 0; i < S.length; i++) if (((t >> 1) + i * 3) % 8 < 2) spark(g, S[i][0], S[i][1], cICE, .6);
    },
    "drizzle": function (g, t) { shape(g, TOP, W); drops(g, [[3, 0], [8, 4], [13, 2], [18, 6]], 11, t, cDROP, 0, 1, false); },
    "rain": function (g, t) { shape(g, TOP, W); drops(g, RAIN, 11, t, cDROP, 2, 0, false); },
    "heavy-rain": function (g, t) { shape(g, TOP, "#C3CBCB"); drops(g, HEAVY, 11, t, cDROP, 3, 0, true); },
    "freezing-drizzle": function (g, t) { shape(g, TOP, W); drops(g, [[3, 0], [8, 4], [13, 2], [18, 6]], 11, t, cICE, 0, 1, false); iceRow(g, 17, t); },
    "freezing-rain": function (g, t) {                            // freezing rain: drops hit the crust and freeze
      shape(g, TOP, W); drops(g, RAIN, 11, t, cICE, 2, 0, false); iceRow(g, 17, t);
      for (var i = 0; i < RAIN.length; i++) if ((t + RAIN[i][1]) % 10 === 6) spark(g, 16, RAIN[i][0], cW, .8);
    },
    "showers-day": function (g, t) { peek(g, t, false); drops(g, RAIN_SH, 12, t, cDROP, 2, 0, false); },
    "showers-night": function (g, t) { peek(g, t, true); drops(g, RAIN_SH, 12, t, cDROP, 2, 0, false); },
    "sleet": function (g, t) {                                    // sleet: drops and flakes mixed
      shape(g, TOP, W);
      drops(g, [[2, 0], [9, 5], [16, 2]], 11, t, cDROP, 2, 0, false);
      flakes(g, [[5, 0], [12, 4], [19, 2]], 11, t, 1, true);
      shape(g, TOP, W);
    },
    "snow": function (g, t) { flakes(g, SNOW, 11, t, 1, true); shape(g, TOP, W); },
    "heavy-snow": function (g, t) { flakes(g, SNOW_H, 11, t, 0, true); shape(g, TOP, "#C3CBCB"); },
    "snow-grains": function (g, t) { shape(g, TOP, W); flakes(g, [[2, 0], [5, 3], [8, 6], [11, 1], [14, 4], [17, 7], [20, 2]], 11, t, 0, false); },
    "snow-showers-day": function (g, t) { flakes(g, SNOW_SH, 12, t, 1, true); peek(g, t, false); },
    "snow-showers-night": function (g, t) { flakes(g, SNOW_SH, 12, t, 1, true); peek(g, t, true); },
    "thunderstorm": function (g, t) {
      shape(g, TOP, flash(t) ? W : GREY);
      drops(g, [[2, 0], [5, 4], [15, 2], [18, 6], [20, 1]], 11, t, cDROP, 2, 0, false);
      bolt(g, 9, t);
    },
    "thunderstorm-heavy": function (g, t) {                        // severe storm: dark cloud, slanted downpour, two bolts
      var f2 = (t + 10) % 20 === 0 || (t + 10) % 20 === 2;
      shape(g, TOP, flash(t) || f2 ? W : "#6F7979");
      drops(g, HEAVY, 11, t, cDROP, 3, 0, true);
      bolt(g, 4, t); bolt(g, 13, t + 10);
    },
    "thunderstorm-hail": function (g, t) {
      shape(g, TOP, flash(t) ? W : GREY);
      hail(g, [[2, 0], [15, 4], [18, 8]], 11, t);
      bolt(g, 7, t);
    },
    "wind": function (g, t) { streams(g, t, cW, .6); },
    "blizzard": function (g, t) {                                 // blizzard: snow driven sideways with a trail
      tint(g, "200,210,210", .07);
      var B = [[0, 0], [3, 17], [6, 6], [9, 23], [12, 11], [15, 28], [1, 14], [4, 31], [7, 3], [10, 20], [13, 9], [16, 26]];
      for (var i = 0; i < B.length; i++) {
        var m = (t * 2 + B[i][1]) % 34, x = m - 6, y = (B[i][0] + (m >> 2)) % R;
        put(g, y, x - 2, a(cW, .35)); put(g, y, x - 3, a(cW, .18)); put(g, y, x - 1, a(cW, .6));
        spark(g, y, x, cW, .45);
      }
    },
    "drifting-snow": function (g, t) {                            // drifting snow: sweeps along the ground, wind above
      streams(g, t, "200,210,210", .35, [0, 1]);
      for (var i = 0; i < 9; i++) {
        var m = (t * 3 + i * 11) % 30, x = m - 4, y = 13 + (i % 4) + (m > 14 ? -1 : 0);
        put(g, y, x, W); put(g, y, x - 1, a(cW, .55)); put(g, y, x - 2, a(cW, .3)); put(g, y, x - 3, a(cW, .15));
      }
      for (var x2 = 0; x2 < C; x2++) put(g, 17, x2, a(cW, .35));
    },
    "dust-haze": function (g, t) {                                // dust haze: ochre air, dim sun, hanging dust
      tint(g, cDUST, .2);
      sun(g, 10.5, 8, 4.3, 0, 0, t, .4);
      var D = [[2, 3], [4, 15], [6, 8], [9, 19], [11, 1], [13, 12], [15, 6], [16, 17], [7, 2], [14, 20]];
      for (var i = 0; i < D.length; i++) put(g, D[i][0] + (((t >> 3) + i) % 2), (D[i][1] + (t >> 2) + i * 2) % C, a(cDUST, ((t >> 2) + i) % 4 ? .7 : 1));
    },
    "dust-storm": function (g, t) {                               // dust storm: wind carries streaks of dust
      tint(g, cDUST, .14);
      streams(g, t, cDUST, .4);
      var D = [[1, 0], [3, 9], [5, 4], [7, 13], [9, 2], [11, 8], [13, 15], [15, 5], [17, 11], [2, 18], [6, 21], [10, 25], [14, 20], [16, 27]];
      for (var i = 0; i < D.length; i++) {
        var x = ((t * 2 + D[i][1] * 3) % 32) - 5, y = D[i][0];
        put(g, y, x, a(cDUST, 1)); put(g, y, x - 1, a(cDUST, .65)); put(g, y, x - 2, a(cDUST, .4)); put(g, y, x - 3, a(cDUST, .2));
      }
    },
    "smoke": function (g, t) {                                    // smoke / smog: three wavy plumes rise and fade
      tint(g, "170,168,160", .1);
      var X0 = [4, 10, 16];
      for (var i = 0; i < X0.length; i++) for (var y = 17; y >= 1; y--) {
        var up = 17 - y, x = Math.round(X0[i] + up * 0.3 + Math.sin((y + (t >> 1) + i * 4) * 0.7) * 1.1);
        put(g, y, x, a("205,203,196", (0.95 - up * 0.045).toFixed(2)));
        if (up > 8) put(g, y, x + 1, a("205,203,196", (0.6 - up * 0.03).toFixed(2)));
      }
    },
    "dry-wind": function (g, t) {                                 // dry wind: hot wind under the sun
      tint(g, cHOT, .08);
      sun(g, 16.5, 4.5, 2.6, 3.8, 5.0, t);
      streams(g, t, cHOT, .5, [1, 2]);
      lines(g, [[6, 0, 9]], t, cHOT, 6, 1);
    },
    "haze": function (g, t) {                                     // haze / smog: sun behind bands
      tint(g, "185,179,166", .12);
      sun(g, 10.5, 7.5, 4.6, 0, 0, t, .55);
      lines(g, [[5, 2, 19], [9, 0, 21], [13, 3, 20], [16, 1, 18]], t, "185,179,166", 10, 2);
    },
    "heat": function (g, t) {                                     // heat: sun and shimmering air
      sun(g, 10.5, 6.5, 3.6, 5.2, 6.6, t);
      for (var x = 1; x < 21; x++) for (var j = 0; j < 2; j++)
        put(g, 14 + j * 3 + Math.round(Math.sin((x + t * 0.8 + j * 3) * 0.75) * 0.9), x, a(cHOT, j ? .5 : .85));
    },
    "frost": function (g, t) { crystal(g, 10.5, 9, 7.6, t); },
    "ice": function (g, t) {                                      // black ice: an ice slab with a running glint, a snowflake above
      for (var i = 0; i < 6; i++) { var an = i * Math.PI / 3 + Math.PI / 2; shape(g, ray(10.5, 5.5, 10.5 + 4.6 * Math.cos(an), 5.5 - 4.6 * Math.sin(an)), W); }
      var b = (t % 34) - 6;
      for (var y = 13; y < 17; y++) for (var x = 0; x < C; x++) { var d = x + (y - 13) - b; g[y][x] = d >= 0 && d < 2.5 ? W : a(cICE, y === 13 ? .85 : .45); }
    },
    "not-available": function (g, t) {                            // no data: a scan line sweeps the grid
      var c = (t >> 1) % (C + 6) - 3;
      for (var y = 0; y < R; y++) for (var x = 0; x < C; x++) { var d = Math.abs(x - c); if (d < 3) g[y][x] = a(cW, d < 1 ? .45 : .22); }
    }
  };
  var LIST = [];
  for (var n in K) if (K.hasOwnProperty(n)) LIST.push(n);
  // short aliases
  K.sun = K["clear-day"]; K.moon = K["clear-night"]; K.cloud = K.overcast; K.storm = K.thunderstorm;

  function draw(ctx, kind, t, k) {
    k = k || 1;
    var g = blank(), x, y;
    (K[kind] || K["not-available"])(g, t);
    ctx.clearRect(0, 0, C * P * k, R * P * k);
    for (y = 0; y < R; y++) for (x = 0; x < C; x++) {
      ctx.fillStyle = g[y][x]; ctx.beginPath();
      ctx.arc((x * P + 1) * k, (y * P + 1) * k, 1.1 * k, 0, 2 * Math.PI); ctx.fill();
    }
  }
  return { draw: draw, W: C * P, H: R * P, FRAMES: 12000, STEP: 150, LIST: LIST };
})();
