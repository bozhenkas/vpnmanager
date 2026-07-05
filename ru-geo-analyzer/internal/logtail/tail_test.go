package logtail

import "testing"

func TestParse(t *testing.T) {
	cases := []struct {
		line    string
		ok      bool
		host    string
		proto   string
		out     string
	}{
		{"2026/06/26 22:16:01 from 127.0.0.1:44090 accepted tcp:api2.cursor.sh:443 [GOIDA_SWE -> REMNA_SWE] email: 3",
			true, "api2.cursor.sh", "tcp", "REMNA_SWE"},
		{"... accepted tcp:149.154.167.35:443 [FRA_VLESS_TCP_REALITY_443 >> DIRECT] email: 2",
			true, "149.154.167.35", "tcp", "DIRECT"},
		{"... accepted udp:51.77.75.111:5430 [GOIDA_SWE -> REMNA_SWE] email: 3",
			true, "51.77.75.111", "udp", "REMNA_SWE"},
		{"... accepted tcp:[2a02:6b8::2:242]:443 [GOIDA_FRA -> REMNA_FRA] email: 7",
			true, "2a02:6b8::2:242", "tcp", "REMNA_FRA"},
		{"2026/06/26 22:00:00 some unrelated log line", false, "", "", ""},
	}
	for _, c := range cases {
		h, ok := parse(c.line)
		if ok != c.ok {
			t.Fatalf("parse(%q) ok=%v want %v", c.line, ok, c.ok)
		}
		if !ok {
			continue
		}
		if h.Host != c.host || h.Proto != c.proto || h.Outbound != c.out {
			t.Errorf("parse(%q) = %+v; want host=%s proto=%s out=%s", c.line, h, c.host, c.proto, c.out)
		}
	}
}
