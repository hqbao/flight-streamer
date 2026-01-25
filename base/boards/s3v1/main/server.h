#ifndef SERVER_H
#define SERVER_H

typedef struct {
  int roll;
  int pitch;
  int yaw;
  int throttle;
  int on;
  int mode;
} rc_t;

void server_start(void);
void rc_get(rc_t *rc);

#endif