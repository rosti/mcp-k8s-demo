package main

import (
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"time"
)

// AppConfig holds database connection settings loaded from a mounted secret.
type AppConfig struct {
	DBHost     string `json:"db_host"`
	DBPassword string `json:"db_password"`
	DBName     string `json:"db_name"`
}

// Global state — this is where the subtle bug lives.
// The config is lazily loaded on first real request, NOT at startup.
// This means the pod starts fine, health checks pass, but actual
// traffic triggers the failure.
var (
	config     *AppConfig
	configOnce sync.Once
	configErr  error
)

// configPath is where the app expects the secret to be mounted.
// The Kubernetes manifest mounts it at /etc/secrets — mismatch!
const configPath = "/app/config/db-credentials.json"

func loadConfig() (*AppConfig, error) {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read config from %s: %w", configPath, err)
	}

	var cfg AppConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("failed to parse config: %w", err)
	}

	return &cfg, nil
}

// healthHandler — Kubernetes liveness/readiness probe.
// Returns 200 always. It does NOT touch config.
// This is why Kubernetes thinks the pod is healthy.
func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{
		"status": "healthy",
		"time":   time.Now().Format(time.RFC3339),
	})
}

// orderHandler — simulates a real business endpoint.
// This is where the failure surfaces: it needs the DB config
// to "process" an order, and the config file doesn't exist
// at the expected path.
func orderHandler(w http.ResponseWriter, r *http.Request) {
	// Lazy load: first request triggers config read
	configOnce.Do(func() {
		log.Println("Loading database configuration...")
		config, configErr = loadConfig()
		if configErr != nil {
			log.Printf("CONFIG ERROR: %v", configErr)
		} else {
			log.Printf("Config loaded successfully: host=%s db=%s", config.DBHost, config.DBName)
		}
	})

	if configErr != nil {
		// The error is persistent after first failure (sync.Once).
		// Every subsequent request also fails — but intermittently
		// from the outside because health checks still pass.
		log.Printf("Request failed — config unavailable: %v", configErr)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":   "internal configuration error",
			"message": "unable to process order — database configuration unavailable",
			// Deliberately vague — doesn't leak the path.
			// This is realistic: production apps don't expose internals.
		})
		return
	}

	// Simulate successful order processing
	orderID := fmt.Sprintf("ORD-%d-%04d", time.Now().Unix(), rand.Intn(10000))
	log.Printf("Order processed successfully: %s (db=%s)", orderID, config.DBHost)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"order_id": orderID,
		"status":   "confirmed",
		"message":  "order processed successfully",
	})
}

// metricsHandler — bonus: shows request counts.
// In a real demo, this gives the LLM another signal to reason about.
var (
	totalRequests  int
	failedRequests int
	mu             sync.Mutex
)

func metricsHandler(w http.ResponseWriter, r *http.Request) {
	mu.Lock()
	defer mu.Unlock()
	// Prometheus exposition format — no external dependencies needed
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	fmt.Fprintf(w, "# HELP order_service_total_requests Total number of requests to /api/orders\n")
	fmt.Fprintf(w, "# TYPE order_service_total_requests counter\n")
	fmt.Fprintf(w, "order_service_total_requests %d\n", totalRequests)
	fmt.Fprintf(w, "# HELP order_service_failed_requests Total number of failed requests to /api/orders\n")
	fmt.Fprintf(w, "# TYPE order_service_failed_requests counter\n")
	fmt.Fprintf(w, "order_service_failed_requests %d\n", failedRequests)
	fmt.Fprintf(w, "# HELP order_service_success_requests Total number of successful requests\n")
	fmt.Fprintf(w, "# TYPE order_service_success_requests counter\n")
	fmt.Fprintf(w, "order_service_success_requests %d\n", totalRequests-failedRequests)
	errorRate := float64(failedRequests) / max(float64(totalRequests), 1) * 100
	fmt.Fprintf(w, "# HELP order_service_error_rate_percent Current error rate percentage\n")
	fmt.Fprintf(w, "# TYPE order_service_error_rate_percent gauge\n")
	fmt.Fprintf(w, "order_service_error_rate_percent %.1f\n", errorRate)
}

func withMetrics(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		totalRequests++
		mu.Unlock()

		// Wrap response writer to capture status code
		rec := &statusRecorder{ResponseWriter: w, statusCode: 200}
		next(rec, r)

		if rec.statusCode >= 400 {
			mu.Lock()
			failedRequests++
			mu.Unlock()
		}
	}
}

type statusRecorder struct {
	http.ResponseWriter
	statusCode int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.statusCode = code
	r.ResponseWriter.WriteHeader(code)
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthHandler)
	mux.HandleFunc("/readyz", healthHandler)
	mux.HandleFunc("/api/orders", withMetrics(orderHandler))
	mux.HandleFunc("/metrics", metricsHandler)

	log.Printf("=== Order Service starting on :%s ===", port)
	log.Printf("Health endpoint: /healthz")
	log.Printf("Order endpoint:  /api/orders")
	log.Printf("Config path:     %s", configPath)

	server := &http.Server{
		Addr:         ":" + port,
		Handler:      mux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("Server failed: %v", err)
	}
}
