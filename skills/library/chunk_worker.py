#!/usr/bin/env python3
"""
Chunk处理队列工作者
由cron定时调用，处理队列中的文档chunk生成
支持暂停/恢复功能
"""
import sys
import json
import time
from pathlib import Path

# 添加library路径
sys.path.insert(0, str(Path(__file__).parent))

from app import (
    load_chunk_queue, save_chunk_queue, 
    process_document_chunks, load_index, save_index,
    CHUNK_PROGRESS_DIR
)

PAUSE_FILE = CHUNK_PROGRESS_DIR / ".pause"

def is_paused():
    """检查是否暂停"""
    return PAUSE_FILE.exists()

def process_queue():
    """处理chunk队列"""
    # 检查暂停标志
    if is_paused():
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 处理已暂停")
        return
    
    queue = load_chunk_queue()
    
    if not queue:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 队列为空")
        return
    
    # 找到第一个pending任务
    for i, item in enumerate(queue):
        if item.get('status') == 'pending':
            doc_id = item['doc_id']
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 处理队列中的文档: {doc_id}")
            
            # 更新状态为processing
            queue[i]['status'] = 'processing'
            queue[i]['started_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            save_chunk_queue(queue)
            
            # 更新文档状态
            index = load_index()
            for d in index['documents']:
                if d['id'] == doc_id:
                    d['chunk_status'] = 'processing'
                    save_index(index)
                    break
            
            # 处理chunk
            try:
                # 在处理过程中也要检查暂停标志
                process_document_chunks_with_pause_check(doc_id)
                
                # 处理完成，从队列移除
                queue = load_chunk_queue()
                queue = [q for q in queue if q.get('doc_id') != doc_id]
                save_chunk_queue(queue)
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 文档处理完成: {doc_id}")
                
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 处理失败: {doc_id}, 错误: {e}")
                # 更新状态为pending（可以重试）
                queue = load_chunk_queue()
                for item in queue:
                    if item.get('doc_id') == doc_id:
                        item['status'] = 'pending'
                        item['error'] = str(e)
                        break
                save_chunk_queue(queue)
            
            return  # 只处理一个
    
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 没有待处理的文档")


def process_document_chunks_with_pause_check(doc_id):
    """处理chunk，带暂停检查"""
    # 每次处理前检查暂停标志
    if is_paused():
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 处理被暂停中断: {doc_id}")
        # 更新状态为 paused
        index = load_index()
        for d in index['documents']:
            if d['id'] == doc_id:
                d['chunk_status'] = 'paused'
                save_index(index)
                break
        # 更新队列状态
        queue = load_chunk_queue()
        for item in queue:
            if item.get('doc_id') == doc_id:
                item['status'] = 'pending'
                item['paused_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                break
        save_chunk_queue(queue)
        return
    
    # 调用原来的处理函数
    process_document_chunks(doc_id)


if __name__ == '__main__':
    process_queue()
