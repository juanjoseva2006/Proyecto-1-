#ifndef AUTH_H
#define AUTH_H
/* NULL means success; otherwise a protocol error code. */
const char *authenticate(const char *email, const char *password, char profile[8]);
#endif
