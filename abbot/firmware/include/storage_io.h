/**
 * storage_io.h —— 存储任务请求/应答契约（TF 卡独占访问的唯一投递口）
 *
 * 契约出处: abbot/doc/02 §4.2（跨核队列规范 —— TF 卡独占访问由 task_storage 单线程串行化）
 *
 * 为什么要单独一个头：
 *   UI 侧读卡层 `src/ui/ui_data.c` 必须是**纯 C**（主机侧可单测），不能包含 Arduino.h；
 *   但它要往存储队列投递请求、等应答，就得看到 `storage_req_t` 与队列句柄。
 *   因此把「存储契约」从 hal_shared.h 里单独拆出来，本头**只依赖 FreeRTOS 与标准 C**。
 *
 * 铁律:
 *   1. 任何模块都**不得**在 UI 任务里直接调 hal_storage_* —— 那会与 task_storage 抢 TF 卡总线。
 *   2. 需要读卡的模块走本契约：投递请求 → 等信号量 → 读应答块 → 释放信号量。
 *   3. 应答数据放在**调用方持有的缓冲**里（storage_io_t 由调用方分配），
 *      队列只复制请求本身，存储任务把结果写进调用方的缓冲。
 */
#ifndef ABBOT_STORAGE_IO_H
#define ABBOT_STORAGE_IO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>

#ifdef __cplusplus
extern "C" {
#endif

/* TF 卡路径上限（含结尾 NUL） */
#define STORAGE_PATH_MAX 72

/* 存储操作码 */
typedef enum {
    STORAGE_OP_WRITE_TEXT = 1,  /* 原子写文本（旧路径，text+len，无应答） */
    STORAGE_OP_DELETE = 2,      /* 删除文件（旧路径，无应答） */
    STORAGE_OP_READ_TEXT = 3,   /* 读文本到 text（旧路径，无应答） */
    STORAGE_OP_WRITE_ASSET = 4, /* 写资产文件（net 任务用，走 asset 作业） */
    /* ---- 以下为 UI 数据读取层的带应答操作 ---- */
    STORAGE_OP_READ_TEXT_IO = 5,  /* 读文本到 io->buf（最多 buf_cap-1 字节，补 NUL） */
    STORAGE_OP_READ_RANGE = 6,    /* 读区间到 io->buf（offset/len） */
    STORAGE_OP_LIST_FILES = 7,    /* 列目录到 io->list_buf（每行一个名字，'\n' 分隔） */
    STORAGE_OP_GET_SIZE = 8,      /* 取文件大小 -> io->out_size */
    STORAGE_OP_WRITE_TEXT_IO = 9, /* 原子写文本（text+len）-> io->result */
    STORAGE_OP_DIR_USAGE = 10,    /* 目录占用 -> io->dir_bytes */
    STORAGE_OP_IS_MOUNTED = 11    /* 是否挂载 -> io->mounted */
} storage_op_t;

/**
 * 资产写入作业 —— net 任务把下载好的大文件交给存储任务落盘。
 *
 * 为什么绕这一圈：TF 卡由 `task_storage` 独占串行化访问（doc/02 §4.2），
 * net 任务直接写卡会与存储任务抢总线。
 */
typedef struct {
    uint8_t *data;         /* 数据缓冲（所有权随作业转移，写完由存储任务释放） */
    uint32_t len;
    int32_t result;        /* HAL_OK 或错误码 */
    SemaphoreHandle_t done; /* 存储任务写完后 give，net 任务 take */
} storage_asset_job_t;

/**
 * 应答块 —— **由调用方分配**（栈上或静态），存储任务把结果写在这里。
 * 队列只复制请求本身，因此结果必须落在调用方的内存里。
 */
typedef struct {
    /* 读缓冲（调用方提供，容量 buf_cap） */
    uint8_t *buf;
    uint32_t buf_cap;
    uint32_t out_len;   /* 实际写入字节数 */
    /* 列目录结果（调用方提供，容量 list_cap；每行一个文件名，'\n' 分隔） */
    char *list_buf;
    uint32_t list_cap;
    uint32_t list_count;
    /* 其它标量结果 */
    uint32_t out_size;  /* 文件大小 */
    uint64_t dir_bytes; /* 目录占用 */
    int32_t result;     /* HAL_OK(0) 或错误码 */
    uint8_t mounted;    /* TF 卡是否挂载 */
} storage_io_t;

/** 存储请求 —— 投递给 `hal_storage_req_queue()`。 */
typedef struct {
    uint8_t op;               /* storage_op_t */
    char path[STORAGE_PATH_MAX];
    uint32_t offset;          /* 读区间起点 */
    uint32_t len;             /* 写文本长度 / 读区间长度 */
    char text[1024];          /* 写文本正文（op=1/9） */
    storage_asset_job_t *asset; /* op=4 的资产作业 */
    storage_io_t *io;         /* op>=5 的应答块（调用方持有） */
    SemaphoreHandle_t reply;  /* 应答信号量（op>=5 时非空） */
} storage_req_t;

/**
 * 存储请求队列（TF 卡访问的唯一投递口）。
 * 队列里只放 `storage_req_t`。
 */
QueueHandle_t hal_storage_req_queue(void);

#ifdef __cplusplus
}
#endif

#endif /* ABBOT_STORAGE_IO_H */
