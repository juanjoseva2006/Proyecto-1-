#define _POSIX_C_SOURCE 200809L
#include "auth.h"
#include <arpa/inet.h>
#include <curl/curl.h>
#include <errno.h>
#include <limits.h>
#include <netdb.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define MAX_NODES 256
#define MAX_CLIENTS 128
#define HISTORY 5
#define LINE 1024
typedef struct { time_t at; int cpu, temperature; char state[8]; } Sample;
typedef struct { time_t at; int id; char code[16], detail[81]; } Event;
typedef struct {
    char name[33]; bool connected; double last_seen;
    Sample samples[HISTORY]; int samples_count;
    Event events[HISTORY]; int events_count, highest_event;
} Node;
typedef struct { int fd, node, role, highest_id; char peer[128]; } Session;
static Node nodes[MAX_NODES];
static int node_count, connections;
static pthread_mutex_t state_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t log_lock = PTHREAD_MUTEX_INITIALIZER;
static FILE *log_file;
static volatile sig_atomic_t stopping;

static double monotonic_now(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void log_line(const char *peer, const char *direction, const char *message) {
    /* Never log an incoming body: malformed AUTH must not leak credentials. */
    time_t now = time(NULL); struct tm tm; char stamp[32];
    localtime_r(&now, &tm); strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%S%z", &tm);
    pthread_mutex_lock(&log_lock);
    fprintf(stdout, "%s [%s] %s %s\n", stamp, peer, direction, message);
    fprintf(log_file, "%s [%s] %s %s\n", stamp, peer, direction, message);
    fflush(stdout); fflush(log_file);
    pthread_mutex_unlock(&log_lock);
}
static bool reply(Session *s, const char *format, ...) {
    char data[LINE+1]; va_list ap;
    va_start(ap, format); int n = vsnprintf(data, sizeof(data)-1, format, ap); va_end(ap);
    if (n < 0 || n >= LINE) return false;
    log_line(s->peer, "TX", data);
    data[n++] = '\n';
    int done = 0;
    while (done < n) {
        ssize_t sent = send(s->fd, data+done, (size_t)(n-done), MSG_NOSIGNAL);
        if (sent < 0 && errno == EINTR) continue;
        if (sent <= 0) return false;
        done += (int)sent;
    }
    return true;
}
static bool error_reply(Session *s, int id, const char *code) {
    return reply(s, "ERROR|%d|%s|Peticion_rechazada", id, code);
}
static bool integer(const char *s, int low, int high, int *result) {
    if (!*s) return false;
    const char *p = s;
    if (*p == '-' && low < 0) p++;
    if (!*p) return false;
    for (; *p; p++) if (*p < '0' || *p > '9') return false;
    errno = 0; char *end; long n = strtol(s, &end, 10);
    if (errno || *end || n < low || n > high) return false;
    *result = (int)n; return true;
}
static bool name_valid(const char *s) {
    size_t n = strlen(s); if (!n || n > 32) return false;
    for (; *s; s++) if (!((*s >= 'a' && *s <= 'z') || (*s >= 'A' && *s <= 'Z') ||
        (*s >= '0' && *s <= '9') || *s == '_' || *s == '-')) return false;
    return true;
}
static bool email_valid(const char *s) {
    size_t n = strlen(s); const char *at = strchr(s, '@');
    if (n < 3 || n > 254 || !at || at == s || !at[1] || strchr(at+1, '@')) return false;
    for (; *s; s++) if (*s <= ' ' || *s > '~') return false;
    return true;
}
static int find_node(const char *name) {
    for (int i = 0; i < node_count; i++) if (!strcmp(nodes[i].name, name)) return i;
    return -1;
}
static bool handle(Session *s, char *line) {
    char original[LINE+1]; snprintf(original, sizeof(original), "%s", line);
    char *f[9]; int count = 1; f[0] = line;
    for (char *p = line; *p; p++) if (*p == '|') {
        *p = 0;
        if (count == 9) { log_line(s->peer, "RX", "INVALID [contenido omitido]"); return error_reply(s, 0, "BAD_FORMAT"); }
        f[count++] = p+1;
    }
    int id = 0;
    if (count < 2 || !integer(f[1], 1, INT_MAX, &id)) {
        log_line(s->peer, "RX", "INVALID [contenido omitido]");
        return error_reply(s, 0, "BAD_FORMAT");
    }
    bool known = !strcmp(f[0], "REGISTER") || !strcmp(f[0], "STATUS") ||
                 !strcmp(f[0], "EVENT") || !strcmp(f[0], "AUTH") || !strcmp(f[0], "QUERY");
    /* Log raw requests only after complete validation (below). */
    char summary[96]; snprintf(summary, sizeof(summary), "peticion id=%d tipo=%.8s [campos omitidos]", id, known ? f[0] : "INVALID");
    log_line(s->peer, "RX", summary);
    for (int i = 0; i < count; i++) if (!*f[i]) return error_reply(s, id, "BAD_FORMAT");
    bool event = !strcmp(f[0], "EVENT");
    if (id <= s->highest_id && !event) return error_reply(s, id, "BAD_VALUE");
    if (id > s->highest_id) s->highest_id = id;
    if (!strcmp(f[0], "REGISTER")) {
        if (count != 3) return error_reply(s, id, "BAD_FORMAT");
        if (!name_valid(f[2])) return error_reply(s, id, "BAD_VALUE");
        if (s->role) return error_reply(s, id, "INVALID_STATE");
        pthread_mutex_lock(&state_lock);
        int index = find_node(f[2]); const char *err = NULL;
        if (index >= 0 && nodes[index].connected) err = "NODE_IN_USE";
        else if (index < 0 && node_count == MAX_NODES) err = "INTERNAL";
        else {
            if (index < 0) { index = node_count++; strcpy(nodes[index].name, f[2]); }
            nodes[index].connected = true;
            nodes[index].last_seen = 0; /* old samples do not make a new connection active */
            s->node = index; s->role = 1;
        }
        pthread_mutex_unlock(&state_lock);
        if (err) return error_reply(s, id, err);
        log_line(s->peer, "RX_VALID", original);
        return reply(s, "ACK|%d|REGISTER|OK", id);
    }
    if (!strcmp(f[0], "AUTH")) {
        if (count != 4) return error_reply(s, id, "BAD_FORMAT");
        if (!email_valid(f[2]) || strlen(f[3]) > 64) return error_reply(s, id, "BAD_VALUE");
        if (s->role) return error_reply(s, id, "INVALID_STATE");
        char profile[8]; const char *err = authenticate(f[2], f[3], profile);
        memset(f[3], 0, strlen(f[3])); memset(original, 0, sizeof(original));
        if (err) return error_reply(s, id, err);
        s->role = !strcmp(profile, "ADMIN") ? 3 : 2;
        return reply(s, "ACK|%d|AUTH|%s", id, profile);
    }
    if (!strcmp(f[0], "STATUS") || event) {
        if (count != (event ? 5 : 6)) return error_reply(s, id, "BAD_FORMAT");
        if (s->role != 1) return error_reply(s, id, "INVALID_STATE");
        if (strcmp(f[2], nodes[s->node].name)) return error_reply(s, id, "UNKNOWN_NODE");
        int cpu = 0, temp = 0;
        if (event) {
            if ((strcmp(f[3], "FALLA") && strcmp(f[3], "UMBRAL") && strcmp(f[3], "CAMBIO_ESTADO")) ||
                strlen(f[4]) > 80 || strchr(f[4], ';')) return error_reply(s, id, "BAD_VALUE");
        } else if (!integer(f[3], 0, 100, &cpu) || !integer(f[4], -50, 150, &temp) ||
                   (strcmp(f[5], "NORMAL") && strcmp(f[5], "ALERTA"))) return error_reply(s, id, "BAD_VALUE");
        pthread_mutex_lock(&state_lock);
        Node *n = &nodes[s->node];
        if (event && id > n->highest_event) {
            if (n->events_count == HISTORY) {
                memmove(n->events, n->events+1, (HISTORY-1)*sizeof(Event)); n->events_count--;
            }
            Event *e = &n->events[n->events_count++]; e->at = time(NULL); e->id = id;
            strcpy(e->code, f[3]); strcpy(e->detail, f[4]); n->highest_event = id;
        } else if (!event) {
            if (n->samples_count == HISTORY) {
                memmove(n->samples, n->samples+1, (HISTORY-1)*sizeof(Sample)); n->samples_count--;
            }
            Sample *sample = &n->samples[n->samples_count++];
            sample->at = time(NULL); sample->cpu = cpu; sample->temperature = temp;
            strcpy(sample->state, f[5]); n->last_seen = monotonic_now();
        }
        pthread_mutex_unlock(&state_lock);
        log_line(s->peer, "RX_VALID", original);
        return !event || reply(s, "ACK|%d|EVENT|OK", id);
    }
    if (!strcmp(f[0], "QUERY")) {
        if (count != 4) return error_reply(s, id, "BAD_FORMAT");
        if (s->role < 2) return error_reply(s, id, "INVALID_STATE");
        bool events = !strcmp(f[2], "EVENTS"), current = !strcmp(f[2], "CURRENT");
        if ((!events && !current && strcmp(f[2], "HISTORY")) || !name_valid(f[3])) return error_reply(s, id, "BAD_VALUE");
        if (events && s->role != 3) return error_reply(s, id, "FORBIDDEN");
        Node copy;
        pthread_mutex_lock(&state_lock);
        int index = find_node(f[3]); if (index >= 0) copy = nodes[index];
        pthread_mutex_unlock(&state_lock);
        if (index < 0) return error_reply(s, id, "UNKNOWN_NODE");
        log_line(s->peer, "RX_VALID", original);
        const char *link = !copy.connected ? "DESCONECTADO" :
            (copy.last_seen > 0 && monotonic_now()-copy.last_seen < 15 ? "ACTIVO" : "SIN_DATOS");
        int count_rows = events ? copy.events_count : copy.samples_count;
        if (!count_rows) return reply(s, "RESPONSE|%d|%s|%s|1|-", id, f[2], f[3]);
        for (int i = current ? count_rows-1 : 0; i < count_rows; i++) {
            bool ok;
            if (events) {
                Event *e = &copy.events[i];
                ok = reply(s, "RESPONSE|%d|%s|%s|%d|%lld;%d;%s;%s", id, f[2], f[3], i == count_rows-1,
                           (long long)e->at, e->id, e->code, e->detail);
            } else {
                Sample *p = &copy.samples[i];
                ok = reply(s, "RESPONSE|%d|%s|%s|%d|%lld;%d;%d;%s;%s", id, f[2], f[3], i == count_rows-1,
                           (long long)p->at, p->cpu, p->temperature, p->state, link);
            }
            if (!ok) return false;
        }
        return true;
    }
    return error_reply(s, id, "BAD_FORMAT");
}
static void *serve(void *arg) {
    Session *s = arg; char line[LINE+1]; size_t used = 0; double start = 0;
    bool alive = true;
    log_line(s->peer, "CONNECT", "Conexion_abierta");
    while (alive && !stopping) {
        struct pollfd pfd = { .fd = s->fd, .events = POLLIN };
        int rc = poll(&pfd, 1, 200);
        if (rc < 0) { if (errno == EINTR) continue; break; }
        if (used && monotonic_now()-start >= 5) {
            log_line(s->peer, "CLOSE", "Timeout_linea_incompleta"); break;
        }
        if (!rc) continue;
        char chunk[2048]; ssize_t got = recv(s->fd, chunk, sizeof(chunk), 0);
        if (got <= 0) break;
        for (ssize_t i = 0; i < got && alive; i++) {
            unsigned char ch = (unsigned char)chunk[i];
            if (!used) start = monotonic_now();
            if (ch == '\n') {
                line[used] = 0; alive = handle(s, line);
                memset(line, 0, sizeof(line)); used = 0;
            } else if (ch < 32 || ch > 126) {
                error_reply(s, 0, "BAD_FORMAT"); alive = false;
            } else if (used >= LINE-1) {
                log_line(s->peer, "CLOSE", "Linea_demasiado_larga"); alive = false;
            } else line[used++] = (char)ch;
        }
        memset(chunk, 0, sizeof(chunk));
    }
    pthread_mutex_lock(&state_lock);
    if (s->node >= 0) nodes[s->node].connected = false;
    pthread_mutex_unlock(&state_lock);
    log_line(s->peer, "CLOSE", "Conexion_cerrada");
    close(s->fd); free(s);
    pthread_mutex_lock(&state_lock); connections--; pthread_mutex_unlock(&state_lock);
    return NULL;
}
static void stop(int sig) { (void)sig; stopping = 1; }
int main(int argc, char **argv) {
    int port;
    if (argc != 3 || !integer(argv[1], 1, 65535, &port)) {
        fprintf(stderr, "Uso: %s puerto archivoDeLogs\n", argv[0]); return 2;
    }
    log_file = fopen(argv[2], "a"); if (!log_file) { perror("logs"); return 1; }
    if (curl_global_init(CURL_GLOBAL_DEFAULT)) return 1;
    signal(SIGPIPE, SIG_IGN); signal(SIGINT, stop); signal(SIGTERM, stop);
    const char *host = getenv("MONITOR_BIND_HOST");
    struct addrinfo hints = {0}, *addresses = NULL;
    hints.ai_family = AF_UNSPEC; hints.ai_socktype = SOCK_STREAM; hints.ai_flags = AI_PASSIVE;
    int listener = -1;
    while (!stopping && listener < 0) {
        int rc = getaddrinfo(host && *host ? host : NULL, argv[1], &hints, &addresses);
        if (rc) { log_line("server", "DNS", gai_strerror(rc)); sleep(5); continue; }
        for (struct addrinfo *a = addresses; a; a = a->ai_next) {
            listener = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
            if (listener < 0) continue;
            int yes = 1; setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
            if (!bind(listener, a->ai_addr, a->ai_addrlen) && !listen(listener, 128)) break;
            close(listener); listener = -1;
        }
        freeaddrinfo(addresses);
        if (listener < 0) { log_line("server", "RETRY", "No_se_pudo_abrir_puerto"); sleep(5); }
    }
    log_line("server", "READY", "Servidor_TCP");
    while (!stopping) {
        struct pollfd pfd = { .fd = listener, .events = POLLIN };
        if (poll(&pfd, 1, 200) <= 0) continue;
        struct sockaddr_storage address; socklen_t length = sizeof(address);
        int fd = accept(listener, (struct sockaddr *)&address, &length);
        if (fd < 0) continue;
        struct timeval timeout = { .tv_sec = 5 };
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        Session *s = calloc(1, sizeof(*s));
        if (!s) { close(fd); continue; }
        s->fd = fd; s->node = -1;
        char ip[80], service[16];
        if (!getnameinfo((struct sockaddr *)&address, length, ip, sizeof(ip), service, sizeof(service), NI_NUMERICHOST|NI_NUMERICSERV))
            snprintf(s->peer, sizeof(s->peer), "%s:%s", ip, service);
        pthread_mutex_lock(&state_lock);
        bool full = connections >= MAX_CLIENTS; if (!full) connections++;
        pthread_mutex_unlock(&state_lock);
        if (full) { error_reply(s, 0, "INTERNAL"); close(fd); free(s); continue; }
        pthread_t thread;
        if (pthread_create(&thread, NULL, serve, s)) {
            pthread_mutex_lock(&state_lock); connections--; pthread_mutex_unlock(&state_lock);
            close(fd); free(s);
        } else pthread_detach(thread);
    }
    if (listener >= 0) close(listener);
    for (;;) {
        pthread_mutex_lock(&state_lock); int remaining = connections; pthread_mutex_unlock(&state_lock);
        if (!remaining) break;
        struct timespec delay = { .tv_nsec = 100000000 }; nanosleep(&delay, NULL);
    }
    log_line("server", "STOP", "Cierre_ordenado"); fclose(log_file); curl_global_cleanup(); return 0;
}
