/**
 * net_dispatch.c —— 网络请求调度（纯 C，无 Arduino 依赖）
 *
 * 只做一件事：按 `net_request_t.op` 选端点、传参数，**不碰协议细节**。
 * 明文组装、`edu_stage` 注入、加签、加密、验签、解密全在 net_api 层完成
 * （doc/05 §5.1：net 层是注入与加密的唯一职责，UI 不感知、也不得自行拼装）。
 *
 * 纯 C 的意义：主机侧可以给它喂桩实现，验证「op 是否路由到正确的端点」，
 * 不必上真机（见 test/test_net_dispatch.c）。
 */

#include "net_api.h"

#include <stdlib.h>
#include <string.h>

int net_dispatch(const net_request_t *req, char **out_resp_json)
{
    if (req == NULL) {
        return -1;
    }
    int biz = -1;
    char *resp = NULL;
    bool ok = false;

    switch (req->op) {
    case NET_OP_SOLVE:
        ok = net_study_solve(req->session_id, req->jpg, (size_t)req->jpg_len,
                             (const uint8_t *)req->pcm, (size_t)req->pcm_samples,
                             req->hint_level, &biz, &resp);
        break;
    case NET_OP_VOICE:
        ok = net_study_voice(req->context_page, req->pack_id, req->card_id,
                             (const uint8_t *)req->pcm, (size_t)req->pcm_samples,
                             &biz, &resp);
        break;
    case NET_OP_BOOTSTRAP:
        ok = net_sync_bootstrap_auto(&biz, &resp);
        break;
    case NET_OP_PAIR:
        ok = net_pair_with_code(req->pair_code);
        biz = ok ? 0 : -1;
        break;
    case NET_OP_PROFILE:
        /* 档案同步不是一次网络请求，只是把档案灌进 net 层缓存，
         * 由 net 层在组装明文时自动注入（doc/05 §5.1）。 */
        ok = net_profile_set(req->edu_stage, req->enrollment_year, req->remaining_quota);
        biz = ok ? 0 : -1;
        break;
    default:
        return -1;
    }

    if (out_resp_json != NULL) {
        *out_resp_json = resp;   /* 所有权转移给调用方 */
    } else {
        free(resp);
    }
    return ok ? biz : -1;
}
