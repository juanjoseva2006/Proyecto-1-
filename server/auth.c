#define _POSIX_C_SOURCE 200809L
#include "auth.h"
#include <curl/curl.h>
#include <json-c/json.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct body { char data[65537]; size_t used; };
static size_t receive(void *ptr, size_t size, size_t count, void *arg) {
    struct body *b = arg;
    size_t n = size * count;
    if (n > sizeof(b->data) - 1 - b->used) return 0;
    memcpy(b->data + b->used, ptr, n);
    b->used += n;
    b->data[b->used] = 0;
    return n;
}
static struct json_object *field(struct json_object *o, const char *key) {
    struct json_object *v = NULL;
    if (o && json_object_is_type(o, json_type_object))
        json_object_object_get_ex(o, key, &v);
    return v;
}
const char *authenticate(const char *email, const char *password, char profile[8]) {
    const char *base = getenv("SUPABASE_URL"), *key = getenv("SUPABASE_PUBLISHABLE_KEY");
    if (!base || !key || !*key || strlen(base) > 512 || strlen(key) > 2048 ||
        strpbrk(key, "\r\n")) return "AUTH_UNAVAILABLE";
    int secure = strncmp(base, "https://", 8) == 0;
#ifdef TEST_AUTH_HTTP
    if (!secure && strncmp(base, "http://localhost:", 17) != 0) return "AUTH_UNAVAILABLE";
#else
    if (!secure) return "AUTH_UNAVAILABLE";
#endif
    CURL *curl = curl_easy_init();
    if (!curl) return "AUTH_UNAVAILABLE";
    char url[600], header[2100];
    snprintf(url, sizeof(url), "%s/auth/v1/token?grant_type=password", base);
    /* Configuration is a project origin without a trailing slash. */
    if (base[strlen(base)-1] == '/') {
        curl_easy_cleanup(curl);
        return "AUTH_UNAVAILABLE";
    }
    snprintf(header, sizeof(header), "apikey: %s", key);
    struct curl_slist *headers = NULL;
    headers = curl_slist_append(headers, header);
    headers = curl_slist_append(headers, "Content-Type: application/json");
    struct json_object *request = json_object_new_object();
    json_object_object_add(request, "email", json_object_new_string(email));
    json_object_object_add(request, "password", json_object_new_string(password));
    struct body *body = calloc(1, sizeof(*body));
    if (!body) {
        json_object_put(request); curl_slist_free_all(headers); curl_easy_cleanup(curl);
        return "AUTH_UNAVAILABLE";
    }
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json_object_to_json_string_ext(request, JSON_C_TO_STRING_PLAIN));
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, receive);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, body);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 3000L);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 2000L);
    curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 2L);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
    CURLcode rc = curl_easy_perform(curl);
    long status = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
    const char *error = "AUTH_UNAVAILABLE";
    struct json_object *root = NULL;
    if (rc == CURLE_OK && (status == 400 || status == 401 || status == 403 || status == 422))
        error = "AUTH_FAILED";
    if (rc == CURLE_OK && status == 200) {
        root = json_tokener_parse(body->data);
        struct json_object *user = field(root, "user");
        struct json_object *id = field(user, "id");
        struct json_object *role = field(field(user, "app_metadata"), "perfil");
        if (id && json_object_is_type(id, json_type_string) && *json_object_get_string(id)) {
            error = "FORBIDDEN";
            if (role && json_object_is_type(role, json_type_string)) {
                const char *r = json_object_get_string(role);
                if (!strcmp(r, "LECTOR") || !strcmp(r, "ADMIN")) {
                    snprintf(profile, 8, "%s", r);
                    error = NULL;
                }
            }
        }
    }
    if (root) json_object_put(root);
    memset(body, 0, sizeof(*body)); free(body);
    json_object_put(request); curl_slist_free_all(headers); curl_easy_cleanup(curl);
    return error;
}
