import json
import urllib.request
import urllib.parse
import base64
import time

# ==========================================
# 配置参数
# ==========================================
# 源集群配置
SOURCE_HOST = "http://198.18.0.1:1200"
SOURCE_USERNAME = "elastic"
SOURCE_PASSWORD = "xxx"

# 目标集群配置
DEST_HOST = "http://198.18.0.1:9202"
DEST_USERNAME = "elastic"
DEST_PASSWORD = "xxx"
TASK_POLL_INTERVAL = 5
TASK_POLL_MAX_RETRIES = 120

def get_auth_headers(username, password):
    """生成 Basic Auth 请求头"""
    if not username or not password:
        return {'Content-Type': 'application/json'}
    auth_str = f"{username}:{password}"
    b64_auth_str = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
    return {
        'Authorization': f'Basic {b64_auth_str}',
        'Content-Type': 'application/json'
    }

SOURCE_HEADERS = get_auth_headers(SOURCE_USERNAME, SOURCE_PASSWORD)
DEST_HEADERS = get_auth_headers(DEST_USERNAME, DEST_PASSWORD)

def http_request(url, method="GET", data=None, timeout=30, headers=None):
    """使用 urllib 实现的简单 HTTP 请求封装"""
    try:
        req_data = None
        print(f"[HTTP] {method} {url}")
        if data:
            print(f"[HTTP] 请求参数: {json.dumps(data, ensure_ascii=False)}")
            req_data = json.dumps(data).encode('utf-8')
            
        req_headers = headers if headers else {}
        req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode('utf-8'), response.status
    except urllib.error.URLError as e:
        print(f"连接 {url} 出错: {e}")
        error_body = None
        if hasattr(e, 'read'):
            error_body = e.read().decode('utf-8')
            print(error_body)
        return error_body, getattr(e, 'code', 500)

def extract_error_message(response_text, status):
    """提取 Elasticsearch 返回里的关键信息，便于直接定位问题"""
    if not response_text:
        return f"HTTP {status}"

    try:
        error_data = json.loads(response_text).get('error', {})
    except json.JSONDecodeError:
        return response_text

    messages = []
    if isinstance(error_data, dict):
        error_type = error_data.get('type')
        reason = error_data.get('reason')
        caused_by = error_data.get('caused_by', {})

        if error_type:
            messages.append(error_type)
        if reason:
            messages.append(reason)
        if isinstance(caused_by, dict) and caused_by.get('reason'):
            messages.append(f"caused_by: {caused_by['reason']}")

    return " | ".join(messages) if messages else f"HTTP {status}"

def get_indices(host, headers):
    """从集群获取所有索引及其文档数量"""
    print(f"正在从 {host} 获取索引列表...")
    url = f"{host}/_cat/indices?format=json&h=index,docs.count,store.size"
    response, status = http_request(url, headers=headers)
    
    if status != 200 or not response:
        print(f"从 {host} 获取索引列表失败")
        return {}
        
    try:
        indices_data = json.loads(response)
        # 过滤掉以 '.' 开头的系统索引
        indices = {
            item['index']: {
                'count': int(item.get('docs.count', 0)),
                'size': item.get('store.size', '0b')
            } 
            for item in indices_data if not item['index'].startswith('.')
        }
        return indices
    except json.JSONDecodeError:
        print(f"解析 {host} 的响应失败")
        return {}

def get_index_definition(host, index_name, headers):
    """获取单个索引的 settings 和 mappings"""
    url = f"{host}/{urllib.parse.quote(index_name, safe='')}"
    response, status = http_request(url, headers=headers)

    if status != 200 or not response:
        print(f"获取索引 {index_name} 定义失败")
        return None

    try:
        index_data = json.loads(response)
        return index_data.get(index_name)
    except json.JSONDecodeError:
        print(f"解析索引 {index_name} 定义失败")
        return None

def clean_index_settings(settings):
    """清理不能直接用于创建索引的系统字段"""
    index_settings = dict(settings.get('index', {}))
    for key in ('uuid', 'provided_name', 'creation_date', 'version'):
        index_settings.pop(key, None)
    return {'index': index_settings} if index_settings else {}

def index_exists(host, index_name, headers):
    """检查索引是否已存在"""
    url = f"{host}/{urllib.parse.quote(index_name, safe='')}"
    _, status = http_request(url, method="HEAD", headers=headers)
    return status == 200

def create_index(index_name, source_definition):
    """在目标集群创建索引结构"""
    url = f"{DEST_HOST}/{urllib.parse.quote(index_name, safe='')}"
    payload = {}

    cleaned_settings = clean_index_settings(source_definition.get('settings', {}))
    mappings = source_definition.get('mappings', {})

    if cleaned_settings:
        payload['settings'] = cleaned_settings.get('index', {})
    if mappings:
        payload['mappings'] = mappings

    response, status = http_request(url, method="PUT", data=payload, headers=DEST_HEADERS)
    if status in (200, 201):
        print("  -> 已创建目标索引 settings/mappings")
        return True

    print(f"  -> 创建目标索引失败: HTTP {status}")
    if response:
        print(response)
    return False

def wait_for_task(task_id, max_retries=TASK_POLL_MAX_RETRIES):
    """轮询等待重索引任务完成"""
    print(f"  -> 任务已提交，Task ID: {task_id}")
    url = f"{DEST_HOST}/_tasks/{task_id}"
    consecutive_errors = 0

    for attempt in range(1, max_retries + 1):
        response, status = http_request(url, headers=DEST_HEADERS)
        if status == 200 and response:
            consecutive_errors = 0
            try:
                task_info = json.loads(response)
                if task_info.get('error'):
                    print(f"  -> ERROR: 任务执行失败: {extract_error_message(response, status)}")
                    return False

                if task_info.get('completed', False):
                    response_data = task_info.get('response', {})
                    created = response_data.get('created', 0)
                    updated = response_data.get('updated', 0)
                    failures = response_data.get('failures', [])

                    if failures:
                        print(f"  -> ⚠️ 任务完成，但存在 {len(failures)} 个失败记录")
                        first_failure = failures[0]
                        print(f"  -> 首个失败详情: {json.dumps(first_failure, ensure_ascii=False)}")
                        return False
                        
                    print(f"  -> ✅ 任务完成! 总数：{response_data.get('total', 0)} 新增: {created}, 更新: {updated}")
                    return True
                
                # 未完成，打印进度
                task_status = task_info.get('task', {}).get('status', {})
                total = task_status.get('total', 0)
                created = task_status.get('created', 0)
                updated = task_status.get('updated', 0)
                if total > 0:
                    progress = (created + updated) / total * 100
                    print(f"  -> 进度: {progress:.2f}% ({created + updated}/{total})")
                
            except json.JSONDecodeError:
                print("  -> ERROR: 任务状态响应不是合法 JSON，停止等待")
                if response:
                    print(response)
                return False
        else:
            consecutive_errors += 1
            error_message = extract_error_message(response, status)
            print(f"  -> 第 {attempt} 次查询任务状态失败: {error_message}")

            # 404 通常表示任务已结束且结果未持久化，继续轮询没有意义
            if status == 404:
                print("  -> ERROR: 任务已无法继续追踪，请检查目标集群是否启用了任务结果存储或任务是否已提前结束")
                return False

            if consecutive_errors >= 3:
                print("  -> ERROR: 连续多次查询任务状态失败，停止等待")
                return False

        time.sleep(TASK_POLL_INTERVAL)

    print(f"  -> ERROR: 等待任务完成超时，已轮询 {max_retries} 次")
    return False

def migrate_index(index_name, doc_count):
    """使用 reindex 迁移单个索引"""
    print(f"\n正在迁移索引: {index_name} (文档数: {doc_count})")

    if index_exists(DEST_HOST, index_name, DEST_HEADERS):
        print(f"  -> ERROR: 目标索引 {index_name} 已存在，跳过 settings/mappings 创建，直接执行 reindex")
    else:
        source_definition = get_index_definition(SOURCE_HOST, index_name, SOURCE_HEADERS)
        if not source_definition:
            print(f"  -> ERROR: 无法获取源索引 {index_name} 的 settings/mappings，停止迁移")
            return False
        if not create_index(index_name, source_definition):
            print(f"  -> ERROR: 创建目标索引 {index_name} 失败，停止迁移")
            return False
    
    # 构造请求 URL，大数据量开启自动切片和异步任务
    url = f"{DEST_HOST}/_reindex?wait_for_completion=false"
        
    payload = {
        "conflicts": "proceed",
        "source": {
            "remote": {
                "host": "http://host.docker.internal:1200",
                # "host": SOURCE_HOST,
                "username": SOURCE_USERNAME,
                "password": SOURCE_PASSWORD
            },
            "index": index_name
        },
        "dest": {
            "index": index_name,
            "op_type": "create"
        }
    }
    
    response, status = http_request(url, method="POST", data=payload, headers=DEST_HEADERS)
    if status in (200, 201):
        try:
            res_json = json.loads(response)
            task_id = res_json.get('task')
            if task_id:
                return wait_for_task(task_id)
            return True
        except:
            return False
    else:
        print(f"  -> 提交重新索引任务时出错: HTTP {status}")
        return False

def display_len(s):
    """计算包含中文字符的字符串在终端的显示长度 (中文按 2，英文按 1)"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(s))

def ljust_cn(s, width):
    """支持中英文混合的左对齐"""
    s = str(s)
    fill_len = width - display_len(s)
    return s + ' ' * max(0, fill_len)

def main():
    print("="*50)
    print("Elasticsearch 数据迁移工具")
    print(f"源地址: {SOURCE_HOST}")
    print(f"目标地址: {DEST_HOST}")
    print("="*50)
    
    # 1. 获取源集群索引
    source_indices = get_indices(SOURCE_HOST, SOURCE_HEADERS)
    if not source_indices:
        print("在源集群上没有找到索引或连接失败。退出。")
        return
        
    print(f"找到 {len(source_indices)} 个索引需要迁移。")
    for source_index, info in source_indices.items():
        print(f"  - 索引: {source_index}, 数量: {info['count']}, 大小: {info['size']}")
    
    # 2. 迁移每个索引
    for index_name, info in source_indices.items():
        migrate_index(index_name, info['count'])
        
    # 稍等片刻让目标集群刷新
    print("\n等待目标集群刷新 (5 秒)...")
    time.sleep(5)
    
    # 3. 获取目标集群索引进行对比
    dest_indices = get_indices(DEST_HOST, DEST_HEADERS)
    
    # 4. 生成报告
    print("\n" + "="*80)
    print(f"{ljust_cn('索引名称', 40)} | {ljust_cn('源文档数', 12)} | {ljust_cn('目标文档数', 12)} | {ljust_cn('状态', 10)}")
    print("-" * 80)
    
    all_success = True
    for index_name, src_info in source_indices.items():
        src_count = src_info['count']
        dest_info = dest_indices.get(index_name, {'count': 0})
        dest_count = dest_info['count']
        
        status = "✅ 一致" if src_count == dest_count else "❌ 不一致"
        if src_count != dest_count:
            all_success = False
            
        print(f"{ljust_cn(index_name, 40)} | {ljust_cn(src_count, 12)} | {ljust_cn(dest_count, 12)} | {ljust_cn(status, 10)}")
        
    print("="*80)
    if all_success:
        print("🎉 迁移成功完成！所有文档数量均匹配。")
    else:
        print("⚠️ 迁移结束，但部分文档数量不匹配。请检查错误信息。")

if __name__ == "__main__":
    main()
