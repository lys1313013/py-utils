import os
import sqlite3
import shutil
import tempfile
from datetime import datetime, timedelta

def get_screen_time_data():
    # macOS Screen Time 数据库路径
    # 注意：此文件受系统保护，需要终端拥有 "完全磁盘访问权限" (Full Disk Access)
    db_path = os.path.expanduser("~/Library/Application Support/Knowledge/knowledgeC.db")
    
    if not os.path.exists(db_path):
        print(f"错误: 找不到数据库文件 {db_path}")
        return

    # 创建临时副本以避免锁定或直接访问冲突
    with tempfile.NamedTemporaryFile(delete=False) as tmp_db:
        try:
            shutil.copy2(db_path, tmp_db.name)
        except PermissionError:
            print("\n" + "="*60)
            print("❌ 权限不足：无法读取 Screen Time 数据库")
            print("="*60)
            print("请授予终端 '完全磁盘访问权限' (Full Disk Access)。")
            return
        except Exception as e:
            print(f"复制数据库时发生未知错误: {e}")
            return
        
        try:
            conn = sqlite3.connect(tmp_db.name)
            cursor = conn.cursor()
            
            # 时间戳处理 (Core Data epoch: 2001-01-01)
            now = datetime.now()
            today_start_local = datetime(now.year, now.month, now.day)
            core_data_offset = 978307200
            today_start_core_data = today_start_local.timestamp() - core_data_offset
            
            # 修改查询：获取原始明细数据，而非聚合数据
            query = """
            SELECT 
                ZVALUESTRING,
                ZSTARTDATE,
                ZENDDATE
            FROM ZOBJECT
            WHERE ZSTREAMNAME = '/app/usage' 
              AND ZSTARTDATE >= ?
            ORDER BY ZSTARTDATE ASC
            """
            
            cursor.execute(query, (today_start_core_data,))
            rows = cursor.fetchall()
            
            # 应用名称映射表
            app_map = {
                'com.apple.Safari': 'Safari 浏览器',
                'com.google.Chrome': 'Google Chrome',
                'com.apple.finder': 'Finder',
                'com.apple.dt.Xcode': 'Xcode',
                'com.microsoft.VSCode': 'VS Code',
                'com.jetbrains.pycharm': 'PyCharm',
                'com.tencent.xinWeChat': '微信',
                'com.apple.Terminal': '终端',
                'com.googlecode.iterm2': 'iTerm2',
                'com.apple.systempreferences': '系统设置',
                'com.alibaba.Tongyi': '通义千问',
                'com.tencent.WeWorkMac': '企业微信',
                'md.obsidian': 'Obsidian',
                'com.apple.iCal': '日历',
                'com.apple.Notes': '备忘录',
                'com.apple.mail': '邮件'
            }
            
            def get_app_name(bundle_id):
                if not bundle_id: return "未知应用"
                name = app_map.get(bundle_id)
                if name: return name
                
                # 尝试从 Bundle ID 提取
                parts = bundle_id.split('.')
                if len(parts) >= 3:
                    # 例如 com.company.appname -> Appname
                    return parts[-1].capitalize()
                elif len(parts) == 2:
                    return parts[-1].capitalize()
                return bundle_id

            # 数据处理容器
            raw_timeline = [] 
            app_total_duration = {} 
            total_duration_all = 0

            for bundle_id, start_ts, end_ts in rows:
                if not start_ts or not end_ts: continue
                
                duration = end_ts - start_ts
                if duration <= 0: continue
                
                # 转换时间
                start_dt = datetime.fromtimestamp(start_ts + core_data_offset)
                end_dt = datetime.fromtimestamp(end_ts + core_data_offset)
                
                app_name = get_app_name(bundle_id)
                
                # 1. 收集原始数据
                raw_timeline.append({
                    'start': start_dt,
                    'end': end_dt,
                    'app': app_name,
                    'duration': duration
                })
                
                # 2. 累加总时长
                app_total_duration[app_name] = app_total_duration.get(app_name, 0) + duration
                total_duration_all += duration

            # ==========================================
            # 数据清洗：合并连续的相同应用记录
            # ==========================================
            merged_timeline = []
            if raw_timeline:
                # 按开始时间排序（理论上数据库查出来已经是排序的，但保险起见）
                raw_timeline.sort(key=lambda x: x['start'])
                
                current_record = raw_timeline[0]
                
                for next_record in raw_timeline[1:]:
                    # 判断是否同一应用
                    is_same_app = (current_record['app'] == next_record['app'])
                    
                    # 判断时间间隔 (下一条开始 - 上一条结束)
                    gap = (next_record['start'] - current_record['end']).total_seconds()
                    
                    # 合并条件：同一应用 且 间隔小于 60 秒 (可视作连续操作)
                    if is_same_app and gap < 60:
                        # 合并：更新结束时间
                        current_record['end'] = next_record['end']
                        # 更新时长 (包含中间的间隔时间，视作持续使用)
                        current_record['duration'] = (current_record['end'] - current_record['start']).total_seconds()
                    else:
                        # 不合并，保存当前记录，开始新记录
                        merged_timeline.append(current_record)
                        current_record = next_record
                
                # 添加最后一条
                merged_timeline.append(current_record)

            # 格式化时间函数
            def format_duration(seconds):
                h = int(seconds // 3600)
                m = int((seconds % 3600) // 60)
                s = int(seconds % 60)
                if h > 0:
                    return f"{h}小时 {m}分钟 {s}秒"
                elif m > 0:
                    return f"{m}分钟 {s}秒"
                else:
                    return f"{s}秒"

            # ==========================================
            # 输出 Part 1: 汇总统计 (所有 > 1分钟的应用)
            # ==========================================
            print(f"\n📱 今日 ({today_start_local.strftime('%Y-%m-%d')}) 屏幕使用时间统计")
            print("=" * 60)
            print(f"⏱️  总使用时间: {format_duration(total_duration_all)}")
            print("\n📊 应用使用时长排行 (时长 > 1分钟):")
            print("-" * 60)
            print(f"{'应用名称':<25} | {'使用时长'}")
            print("-" * 60)
            
            # 排序并过滤
            sorted_apps = sorted(app_total_duration.items(), key=lambda x: x[1], reverse=True)
            for app_name, duration in sorted_apps:
                if duration < 60: continue # 只显示大于1分钟的
                print(f"{app_name:<25} | {format_duration(duration)}")

            # ==========================================
            # 输出 Part 2: 时间段明细 (Timeline)
            # ==========================================
            print("\n\n🕒 应用使用时间段明细 (连续使用):")
            print("=" * 85)
            print(f"{'开始时间':<10} - {'结束时间':<10} | {'持续时长':<12} | {'应用名称'}")
            print("-" * 85)
            
            for item in merged_timeline:
                # 过滤掉极短的碎片时间 (例如 < 5秒)
                if item['duration'] < 5: continue
                
                start_str = item['start'].strftime('%H:%M:%S')
                end_str = item['end'].strftime('%H:%M:%S')
                dur_str = format_duration(item['duration'])
                
                print(f"{start_str} - {end_str} | {dur_str:<12} | {item['app']}")
                
        except sqlite3.OperationalError as e:
            print(f"❌ 数据库读取错误: {e}")
        finally:
            conn.close()
            try:
                os.unlink(tmp_db.name)
            except:
                pass

if __name__ == "__main__":
    get_screen_time_data()
