import asyncio
from bot.database.main import Database
from sqlalchemy import text

async def main():
    async with Database().session() as s:
        # Schema info
        result = await s.execute(text("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'bought_goods' AND column_name IN ('order_id', 'order_item_id');"))
        print("Columns:")
        for row in result:
            print(f"- bought_goods.{row[0]} is nullable {row[1]}")
            
        # Foreign keys
        result = await s.execute(text("""
            SELECT
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name,
                rc.delete_rule
            FROM 
                information_schema.table_constraints AS tc 
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
                JOIN information_schema.referential_constraints rc
                  ON tc.constraint_name = rc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name='bought_goods';
        """))
        print("\nForeign keys:")
        for row in result:
            print(f"- bought_goods.{row[0]} -> {row[1]}.{row[2]} ON DELETE {row[3]}")
            
        # Indexes
        result = await s.execute(text("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'bought_goods';
        """))
        print("\nIndexes:")
        for row in result:
            print(f"- {row[0]}")

if __name__ == "__main__":
    asyncio.run(main())
