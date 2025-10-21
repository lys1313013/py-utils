import psycopg2
from psycopg2 import sql
import os
from datetime import datetime


def connect_postgresql(host, port, database, user, password):
    """连接PostgreSQL数据库"""
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password
        )
        print("成功连接到PostgreSQL数据库")
        return conn
    except Exception as e:
        print(f"连接数据库失败: {e}")
        return None


def execute_schema_query(conn):
    """执行表结构查询"""
    query = """
            SELECT c.table_name       AS 表名, \
                   c.ordinal_position AS 字段序号, \
                   c.column_name      AS 字段标识, \
                   pgd.description    AS 字段名称, \
                   CASE \
                       WHEN c.data_type = 'character varying' THEN \
                           'varchar(' || c.character_maximum_length || ')' \
                       WHEN c.data_type = 'character' THEN \
                           'char(' || c.character_maximum_length || ')' \
                       WHEN c.data_type = 'numeric' THEN \
                           CASE \
                               WHEN c.numeric_precision IS NOT NULL AND c.numeric_scale IS NOT NULL THEN \
                                   'numeric(' || c.numeric_precision || ',' || c.numeric_scale || ')' \
                               WHEN c.numeric_precision IS NOT NULL THEN \
                                   'numeric(' || c.numeric_precision || ')' \
                               ELSE 'numeric' \
                               END \
                       WHEN c.data_type = 'integer' THEN 'int' \
                       WHEN c.data_type = 'timestamp without time zone' THEN 'timestamp' \
                       WHEN c.data_type = 'time without time zone' THEN 'time' \
                       WHEN c.data_type = 'uuid' THEN 'varchar(38)' \
                       ELSE c.data_type \
                       END            AS 类型长度, \
                   CASE \
                       WHEN c.is_nullable = 'NO' THEN '是' \
                       ELSE '否' \
                       END            AS 非空
            FROM information_schema.columns c \
                     LEFT JOIN \
                 pg_catalog.pg_statio_all_tables st ON \
                     c.table_schema = st.schemaname AND c.table_name = st.relname \
                     LEFT JOIN \
                 pg_catalog.pg_description pgd ON \
                     pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
            WHERE c.table_schema = 'public'
            ORDER BY c.table_name, \
                     c.ordinal_position; \
            """

    try:
        cursor = conn.cursor()
        cursor.execute(query)
        results = cursor.fetchall()
        print(f"成功获取 {len(results)} 条字段记录")
        return results
    except Exception as e:
        print(f"执行查询失败: {e}")
        return None


def generate_markdown_document(results, output_file="database_schema.md"):
    """生成Markdown文档"""

    # 按表名分组数据
    tables_data = {}
    for row in results:
        table_name = row[0]  # 表名
        if table_name not in tables_data:
            tables_data[table_name] = []

        tables_data[table_name].append({
            '字段标识': row[2],
            '字段名称': row[3] if row[3] is not None else 'NULL',
            '类型长度': row[4],
            '非空': row[5]
        })

    # 生成Markdown内容
    markdown_content = f"""# 数据库表结构文档

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

"""
    for table_name, columns in tables_data.items():
        table_content = ""
        table_content += f"# {table_name}\n\n"
        table_content += "| 字段标识 | 字段名称 | 类型（长度） | 非空 |\n"
        table_content += "| :------- | :------- | :----------- | :--- |\n"

        for column in columns:
            # 转义Markdown中的特殊字符
            column_name = str(column['字段名称']).replace('|', '\\|')
            type_length = str(column['类型长度']).replace('|', '\\|')

            table_content += f"| {column['字段标识']} | {column_name} | {type_length} | {column['非空']} |\n"

        table_content += "\n"
        markdown_content += table_content

    # 写入文件
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        print(f"Markdown文档已生成: {output_file}")
        return True
    except Exception as e:
        print(f"写入文件失败: {e}")
        return False


def main():
    """主函数"""
    # 数据库连接配置 - 请根据实际情况修改
    db_config = {
        'host': 'localhost',
        'port': '5432',
        'database': 'dify',
        'user': 'postgres',
        'password': 'difyai123456'
    }

    # 或者从环境变量获取（推荐用于生产环境）
    # db_config = {
    #     'host': os.getenv('DB_HOST', 'localhost'),
    #     'port': os.getenv('DB_PORT', '5432'),
    #     'database': os.getenv('DB_NAME', 'your_database'),
    #     'user': os.getenv('DB_USER', 'your_username'),
    #     'password': os.getenv('DB_PASSWORD', 'your_password')
    # }

    # 输出文件
    output_file = "database_schema.md"

    # 连接数据库
    conn = connect_postgresql(**db_config)
    if conn is None:
        return

    try:
        # 执行查询
        results = execute_schema_query(conn)
        if results is None:
            return

        # 生成Markdown文档
        success = generate_markdown_document(results, output_file)
        if success:
            print("数据库文档生成完成！")
        else:
            print("数据库文档生成失败！")

    finally:
        # 关闭数据库连接
        conn.close()
        print("数据库连接已关闭")


if __name__ == "__main__":
    main()