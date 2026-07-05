// Package detect decides whether a host (domain or IP) is *really* Russian,
// using a weighted consensus of independent, authoritative signals rather than
// a single geoip database (which is exactly what misclassifies servers today).
//
// Signals per IP (all pure stdlib, no external deps):
//   - RDAP   — registry country of the IP block (authoritative ownership).   weight 2
//   - Cymru  — Team Cymru IP→ASN whois over DNS: ASN, allocation CC, RIR.     weight 2
//   - PTR    — reverse DNS ending in .ru/.su (weak hint).                     weight 1
//   - RTT    — optional active TCP round-trip from the RU node (<thresh ms).  weight 2
//
// A host is RU when at least one authoritative signal says RU, RU weight beats
// non-RU weight, and (for domains) all resolved IPs agree.
package detect

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

type Config struct {
	DNSTimeout  time.Duration
	HTTPTimeout time.Duration
	RTTEnable   bool          // active TCP-RTT probe (meaningful only when run on the RU node)
	RTTMaxMs    int           // RTT below this counts as a RU vote
	CacheTTL    time.Duration // how long an IP verdict stays cached
	CacheMax    int           // max cached IP verdicts (memory bound)
}

func Default() Config {
	return Config{
		DNSTimeout:  4 * time.Second,
		HTTPTimeout: 6 * time.Second,
		RTTEnable:   false,
		RTTMaxMs:    18,
		CacheTTL:    24 * time.Hour,
		CacheMax:    50000,
	}
}

// IPVerdict is the per-IP evidence.
type IPVerdict struct {
	IP          string `json:"ip"`
	RDAPCountry string `json:"rdap_country,omitempty"`
	CymruCC     string `json:"cymru_cc,omitempty"`
	CymruASN    string `json:"cymru_asn,omitempty"`
	CymruAS     string `json:"cymru_as_name,omitempty"`
	PTR         string `json:"ptr,omitempty"`
	RTTms       int    `json:"rtt_ms,omitempty"`
	IsRU        bool   `json:"is_ru"`
	Confidence  float64 `json:"confidence"`
	RUSignals   int    `json:"ru_signals"` // independent strong signals voting RU
}

// Verdict is the aggregate decision for a host.
type Verdict struct {
	Host       string      `json:"host"`
	Type       string      `json:"type"` // domain|ip
	IsRU       bool        `json:"is_ru"`
	Confidence float64     `json:"confidence"`
	RUSignals  int         `json:"ru_signals"` // min independent RU signals across the host's IPs
	Reason     string      `json:"reason"`
	IPs        []IPVerdict `json:"ips"`
}

type cacheEntry struct {
	v  IPVerdict
	at time.Time
}

type Detector struct {
	cfg      Config
	resolver *net.Resolver
	http     *http.Client

	mu    sync.Mutex
	cache map[string]cacheEntry
}

func New(cfg Config) *Detector {
	return &Detector{
		cfg:      cfg,
		resolver: &net.Resolver{},
		http:     &http.Client{Timeout: cfg.HTTPTimeout},
		cache:    make(map[string]cacheEntry),
	}
}

// Detect resolves the host (if a domain) and decides RU-ness by consensus.
func (d *Detector) Detect(ctx context.Context, host string, port string) Verdict {
	host = strings.TrimSuffix(strings.ToLower(host), ".")
	v := Verdict{Host: host}

	var ips []net.IP
	if ip := net.ParseIP(host); ip != nil {
		v.Type = "ip"
		ips = []net.IP{ip}
	} else {
		v.Type = "domain"
		rctx, cancel := context.WithTimeout(ctx, d.cfg.DNSTimeout)
		addrs, err := d.resolver.LookupIPAddr(rctx, host)
		cancel()
		if err != nil {
			v.Reason = "dns: " + err.Error()
			return v
		}
		for _, a := range addrs {
			ips = append(ips, a.IP)
		}
	}
	if len(ips) == 0 {
		v.Reason = "no addresses"
		return v
	}

	ruCount := 0
	minSignals := -1
	for _, ip := range ips {
		ipv := d.checkIP(ctx, ip, port)
		v.IPs = append(v.IPs, ipv)
		if ipv.IsRU {
			ruCount++
			if minSignals < 0 || ipv.RUSignals < minSignals {
				minSignals = ipv.RUSignals
			}
		}
	}
	if minSignals > 0 {
		v.RUSignals = minSignals
	}

	// A domain is RU only if ALL its resolved IPs are RU (avoids mixed CDN
	// false positives); a literal IP just uses its own verdict.
	v.IsRU = ruCount == len(v.IPs)
	// Confidence = mean per-IP confidence, scaled by IP agreement.
	var sum float64
	for _, ipv := range v.IPs {
		sum += ipv.Confidence
	}
	v.Confidence = (sum / float64(len(v.IPs))) * (float64(ruCount) / float64(len(v.IPs)))
	if v.IsRU {
		v.Reason = fmt.Sprintf("%d/%d IPs RU", ruCount, len(v.IPs))
	} else {
		v.Reason = fmt.Sprintf("%d/%d IPs RU (need all)", ruCount, len(v.IPs))
	}
	return v
}

func (d *Detector) checkIP(ctx context.Context, ip net.IP, port string) IPVerdict {
	key := ip.String()
	d.mu.Lock()
	if e, ok := d.cache[key]; ok && time.Since(e.at) < d.cfg.CacheTTL {
		d.mu.Unlock()
		return e.v
	}
	d.mu.Unlock()

	ipv := IPVerdict{IP: key}
	// Private / non-global: not a candidate, mark non-RU with low conf.
	if !ip.IsGlobalUnicast() || ip.IsPrivate() {
		ipv.Confidence = 0
		d.put(key, ipv)
		return ipv
	}

	asn, cc, asname := d.cymru(ctx, ip)
	ipv.CymruASN, ipv.CymruCC, ipv.CymruAS = asn, cc, asname
	ipv.RDAPCountry = d.rdap(ctx, ip)
	ipv.PTR = d.ptr(ctx, ip)
	if d.cfg.RTTEnable {
		ipv.RTTms = d.rtt(ip, port)
	}

	rtt := -1
	if d.cfg.RTTEnable {
		rtt = ipv.RTTms
	}
	ipv.IsRU, ipv.Confidence, ipv.RUSignals = Consensus(Signals{
		CymruCC:     cc,
		RDAPCountry: ipv.RDAPCountry,
		PTR:         ipv.PTR,
		RTTms:       rtt,
		RTTMaxMs:    d.cfg.RTTMaxMs,
	})
	d.put(key, ipv)
	return ipv
}

func (d *Detector) put(key string, v IPVerdict) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if len(d.cache) >= d.cfg.CacheMax {
		// crude bound: drop ~10% oldest
		n := d.cfg.CacheMax / 10
		for k := range d.cache {
			delete(d.cache, k)
			n--
			if n <= 0 {
				break
			}
		}
	}
	d.cache[key] = cacheEntry{v: v, at: time.Now()}
}

// cymru queries Team Cymru IP→ASN mapping over DNS TXT.
// origin TXT: "ASN | prefix | CC | RIR | date"; AS TXT adds the AS org name.
func (d *Detector) cymru(ctx context.Context, ip net.IP) (asn, cc, asname string) {
	rctx, cancel := context.WithTimeout(ctx, d.cfg.DNSTimeout)
	defer cancel()
	var qname string
	if v4 := ip.To4(); v4 != nil {
		qname = fmt.Sprintf("%d.%d.%d.%d.origin.asn.cymru.com", v4[3], v4[2], v4[1], v4[0])
	} else {
		qname = reverseNibbles(ip) + ".origin6.asn.cymru.com"
	}
	txts, err := d.resolver.LookupTXT(rctx, qname)
	if err != nil || len(txts) == 0 {
		return "", "", ""
	}
	parts := splitPipe(txts[0])
	if len(parts) >= 3 {
		asn = firstField(parts[0])
		cc = strings.ToUpper(parts[2])
	}
	if asn != "" {
		if asTxt, e := d.resolver.LookupTXT(rctx, "AS"+asn+".asn.cymru.com"); e == nil && len(asTxt) > 0 {
			ap := splitPipe(asTxt[0])
			if len(ap) >= 5 {
				asname = ap[4]
			}
		}
	}
	return asn, cc, asname
}

type rdapResp struct {
	Country  string `json:"country"`
	Entities []struct {
		VCardArray []json.RawMessage `json:"vcardArray"`
	} `json:"entities"`
}

func (d *Detector) rdap(ctx context.Context, ip net.IP) string {
	url := "https://rdap.org/ip/" + ip.String()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return ""
	}
	req.Header.Set("Accept", "application/rdap+json")
	resp, err := d.http.Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return ""
	}
	var r rdapResp
	if json.NewDecoder(resp.Body).Decode(&r) != nil {
		return ""
	}
	return strings.ToUpper(strings.TrimSpace(r.Country))
}

func (d *Detector) ptr(ctx context.Context, ip net.IP) string {
	rctx, cancel := context.WithTimeout(ctx, d.cfg.DNSTimeout)
	defer cancel()
	names, err := d.resolver.LookupAddr(rctx, ip.String())
	if err != nil || len(names) == 0 {
		return ""
	}
	return strings.TrimSuffix(names[0], ".")
}

func (d *Detector) rtt(ip net.IP, port string) int {
	if port == "" {
		port = "443"
	}
	start := time.Now()
	conn, err := net.DialTimeout("tcp", net.JoinHostPort(ip.String(), port), 2*time.Second)
	if err != nil {
		return 0
	}
	conn.Close()
	return int(time.Since(start).Milliseconds())
}

// Signals carries the raw per-IP evidence for the consensus decision.
// RTTms < 0 means the RTT signal is disabled/absent.
type Signals struct {
	CymruCC     string // RIR allocation country from Team Cymru
	RDAPCountry string // registry country from RDAP
	PTR         string // reverse DNS name
	RTTms       int    // active TCP-RTT; <0 = absent
	RTTMaxMs    int
}

// Consensus weighs independent signals. Authoritative country sources (RDAP,
// Cymru) carry weight 2; the PTR hint weight 1; a fast RTT weight 2. A host is
// RU only when at least one authoritative source says RU and RU weight strictly
// beats non-RU weight. Confidence is the RU share of the cast weight.
//
// ruSignals counts how many INDEPENDENT strong signals (Cymru, RDAP, RTT — not
// the weak PTR hint) voted RU. The updater requires ruSignals>=2 for fully
// automatic promotion; a single-signal RU goes to the review queue instead.
func Consensus(s Signals) (isRU bool, confidence float64, ruSignals int) {
	ruW, nonW := 0, 0
	authRU := false
	vote := func(country string, w int) {
		if country == "" {
			return
		}
		if strings.EqualFold(country, "RU") {
			ruW += w
			if w >= 2 {
				authRU = true
				ruSignals++
			}
		} else {
			nonW += w
		}
	}
	vote(s.CymruCC, 2)
	vote(s.RDAPCountry, 2)
	if s.PTR != "" {
		if hasRUSuffix(s.PTR) {
			ruW++
		} else {
			nonW++
		}
	}
	if s.RTTms >= 0 {
		if s.RTTms > 0 && s.RTTms <= s.RTTMaxMs {
			ruW += 2
			ruSignals++
		} else if s.RTTms > s.RTTMaxMs {
			nonW++
		}
	}
	total := ruW + nonW
	if total > 0 {
		confidence = float64(ruW) / float64(total)
	}
	isRU = authRU && ruW > nonW
	return isRU, confidence, ruSignals
}

func hasRUSuffix(ptr string) bool {
	p := strings.ToLower(strings.TrimSuffix(ptr, "."))
	return strings.HasSuffix(p, ".ru") || strings.HasSuffix(p, ".su") || strings.HasSuffix(p, ".рф")
}

func splitPipe(s string) []string {
	parts := strings.Split(s, "|")
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
	}
	return parts
}

func firstField(s string) string {
	f := strings.Fields(s)
	if len(f) == 0 {
		return ""
	}
	return f[0]
}

func reverseNibbles(ip net.IP) string {
	ip16 := ip.To16()
	var sb strings.Builder
	for i := len(ip16) - 1; i >= 0; i-- {
		sb.WriteString(fmt.Sprintf("%x.%x.", ip16[i]&0x0f, ip16[i]>>4))
	}
	return strings.TrimSuffix(sb.String(), ".")
}
