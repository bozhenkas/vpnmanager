// ru-geo-analyzer — a small, low-footprint daemon that watches the RU node's
// xray access.log, finds destinations that were sent to a foreign exit but are
// *actually* Russian (i.e. geoip:ru misses), and writes them to candidates.json
// for human review. It never edits the routing lists itself — promotion is a
// separate, deliberate python step.
package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"ru-geo-analyzer/internal/detect"
	"ru-geo-analyzer/internal/logtail"
	"ru-geo-analyzer/internal/store"
)

type config struct {
	logPath        string
	statePath      string
	candidatesPath string
	workers        int
	minConf        float64
	minHits        int
	recheck        time.Duration
	flushEvery     time.Duration
	maxHosts       int
	fromStart      bool
	rttEnable      bool
	autoMinSignals int
	autoMinHits    int
	autoMinAge     time.Duration
}

func loadConfig() config {
	c := config{
		logPath:        env("RGA_LOG_PATH", "/var/log/remnanode/access.log"),
		statePath:      env("RGA_STATE_PATH", "/var/lib/ru-geo-analyzer/state.json"),
		candidatesPath: env("RGA_CANDIDATES_PATH", "/var/lib/ru-geo-analyzer/candidates.json"),
		workers:        envInt("RGA_WORKERS", 3),
		minConf:        envFloat("RGA_MIN_CONFIDENCE", 0.75),
		minHits:        envInt("RGA_MIN_HITS", 3),
		recheck:        envDur("RGA_RECHECK", 7*24*time.Hour),
		flushEvery:     envDur("RGA_FLUSH_EVERY", 60*time.Second),
		maxHosts:       envInt("RGA_MAX_HOSTS", 100000),
		fromStart:      envBool("RGA_FROM_START", false),
		rttEnable:      envBool("RGA_RTT_ENABLE", true),
		autoMinSignals: envInt("RGA_AUTO_MIN_SIGNALS", 2),
		autoMinHits:    envInt("RGA_AUTO_MIN_HITS", 5),
		autoMinAge:     envDur("RGA_AUTO_MIN_AGE", time.Hour),
	}
	flag.StringVar(&c.logPath, "log", c.logPath, "xray access.log path")
	flag.StringVar(&c.candidatesPath, "candidates", c.candidatesPath, "candidates.json output path")
	flag.BoolVar(&c.fromStart, "from-start", c.fromStart, "read the log from the beginning")
	flag.BoolVar(&c.rttEnable, "rtt", c.rttEnable, "enable active TCP-RTT probe (run on RU node only)")
	flag.Parse()
	return c
}

func main() {
	cfg := loadConfig()
	log.SetFlags(log.LstdFlags | log.Lmsgprefix)
	log.SetPrefix("[ru-geo-analyzer] ")

	if err := os.MkdirAll(dir(cfg.statePath), 0o755); err != nil {
		log.Printf("warn: mkdir state dir: %v", err)
	}

	st := store.New(cfg.statePath, cfg.maxHosts, cfg.recheck)
	dcfg := detect.Default()
	dcfg.RTTEnable = cfg.rttEnable
	det := detect.New(dcfg)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigc := make(chan os.Signal, 1)
	signal.Notify(sigc, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigc
		log.Println("shutting down…")
		cancel()
	}()

	hits := make(chan logtail.Hit, 1024)
	work := make(chan job, 1024)

	// logtail → dedup/observe → enqueue checks
	go func() {
		for h := range hits {
			if skipHost(h) {
				continue
			}
			typ := "domain"
			if isIP(h.Host) {
				typ = "ip"
			}
			if st.Observe(h.Host, typ, h.Port) {
				select {
				case work <- job{host: h.Host, port: h.Port}:
				default: // queue full: drop, it'll be re-observed later
				}
			}
		}
	}()

	// worker pool: bounded concurrency keeps memory + network use predictable
	var wg sync.WaitGroup
	for i := 0; i < cfg.workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range work {
				v := det.Detect(ctx, j.host, j.port)
				st.SetVerdict(j.host, v)
				if v.IsRU {
					log.Printf("RU candidate: %-40s conf=%.2f (%s)", v.Host, v.Confidence, v.Reason)
				}
			}
		}()
	}

	// periodic flush of candidates.json + state
	go func() {
		t := time.NewTicker(cfg.flushEvery)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				flush(st, cfg)
			}
		}
	}()

	log.Printf("watching %s (workers=%d, minConf=%.2f, minHits=%d, tracked=%d)",
		cfg.logPath, cfg.workers, cfg.minConf, cfg.minHits, st.Len())

	err := logtail.Follow(ctx, cfg.logPath, cfg.fromStart, hits)
	close(hits)
	close(work)
	wg.Wait()
	flush(st, cfg)
	if err != nil && ctx.Err() == nil {
		log.Fatalf("logtail: %v", err)
	}
	log.Println("stopped.")
}

type job struct {
	host string
	port string
}

func flush(st *store.Store, cfg config) {
	auto := store.AutoPolicy{
		MinSignals: cfg.autoMinSignals,
		MinHits:    cfg.autoMinHits,
		MinAge:     cfg.autoMinAge,
	}
	if err := st.WriteCandidates(cfg.candidatesPath, cfg.minConf, cfg.minHits, auto, time.Now()); err != nil {
		log.Printf("warn: write candidates: %v", err)
	}
	if err := st.Save(); err != nil {
		log.Printf("warn: save state: %v", err)
	}
}

// skipHost drops traffic we should never treat as a candidate:
//   - already routed direct/blocked (not a geoip miss),
//   - private/loopback literal IPs.
func skipHost(h logtail.Hit) bool {
	ob := strings.ToUpper(h.Outbound)
	if ob == "DIRECT" || ob == "BLOCK" || strings.HasPrefix(ob, "DIRECT") {
		return true
	}
	if h.Host == "" {
		return true
	}
	return false
}

func isIP(s string) bool {
	return strings.Count(s, ":") >= 2 || (strings.Count(s, ".") == 3 && strings.IndexFunc(s, func(r rune) bool {
		return r != '.' && (r < '0' || r > '9')
	}) == -1)
}

func dir(p string) string {
	if i := strings.LastIndexByte(p, '/'); i > 0 {
		return p[:i]
	}
	return "."
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
func envFloat(k string, def float64) float64 {
	if v := os.Getenv(k); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}
func envBool(k string, def bool) bool {
	if v := os.Getenv(k); v != "" {
		b, err := strconv.ParseBool(v)
		if err == nil {
			return b
		}
	}
	return def
}
func envDur(k string, def time.Duration) time.Duration {
	if v := os.Getenv(k); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}
