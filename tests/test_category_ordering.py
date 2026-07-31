import pytest
from sqlalchemy import select
from bot.database.main import Database
from bot.database.models.main import Categories
from bot.database.methods.lazy_queries import query_categories
from bot.database.methods.create import create_category

@pytest.fixture(autouse=True)
async def clean_categories():
    """Clean categories before each test"""
    async with Database().session() as s:
        await s.execute(Categories.__table__.delete())
        await s.commit()

@pytest.mark.asyncio
async def test_category_ordering_root_categories(setup_test_database):
    """Test root categories strictly follow display_order"""
    async with Database().session() as db_session:
        c1 = Categories(name="Root A", display_order=20)
        c2 = Categories(name="Root B", display_order=10)
        db_session.add_all([c1, c2])
        await db_session.commit()

    cats = await query_categories(parent_id=None, offset=0, limit=10, count_only=False)
    assert len(cats) >= 2
    cats = [c for c in cats if "Root" in c[1]]
    assert cats[0][1] == "Root B"
    assert cats[1][1] == "Root A"

@pytest.mark.asyncio
async def test_category_ordering_subcategories(setup_test_database):
    """Test subcategories strictly follow display_order inside their parent"""
    async with Database().session() as db_session:
        root = Categories(name="Root C", display_order=10)
        db_session.add(root)
        await db_session.commit()

        sub1 = Categories(name="Sub A", parent_id=root.id, display_order=20)
        sub2 = Categories(name="Sub B", parent_id=root.id, display_order=10)
        db_session.add_all([sub1, sub2])
        await db_session.commit()

    cats = await query_categories(parent_id=root.id, offset=0, limit=10, count_only=False)
    assert len(cats) == 2
    assert cats[0][1] == "Sub B"
    assert cats[1][1] == "Sub A"

@pytest.mark.asyncio
async def test_category_ordering_fallback_id(setup_test_database):
    """Test fallback to id ASC when display_order is identical"""
    async with Database().session() as db_session:
        c1 = Categories(name="SameOrder B", display_order=10)
        c2 = Categories(name="SameOrder A", display_order=10)
        db_session.add(c1)
        await db_session.commit()
        db_session.add(c2)
        await db_session.commit()

    cats = await query_categories(parent_id=None, offset=0, limit=10, count_only=False)
    filtered = [c for c in cats if "SameOrder" in c[1]]
    assert len(filtered) == 2
    assert filtered[0][1] == "SameOrder B" # inserted first -> lower id
    assert filtered[1][1] == "SameOrder A"

@pytest.mark.asyncio
async def test_category_ordering_update(setup_test_database):
    """Test changing a display_order immediately updates the output order"""
    async with Database().session() as db_session:
        c1 = Categories(name="Upd A", display_order=10)
        c2 = Categories(name="Upd B", display_order=20)
        db_session.add_all([c1, c2])
        await db_session.commit()

    cats = await query_categories(parent_id=None, offset=0, limit=10, count_only=False)
    filtered = [c for c in cats if "Upd" in c[1]]
    assert filtered[0][1] == "Upd A"
    assert filtered[1][1] == "Upd B"

    async with Database().session() as db_session:
        result = await db_session.execute(select(Categories).where(Categories.name == "Upd A"))
        c1_upd = result.scalars().first()
        c1_upd.display_order = 30
        await db_session.commit()

    cats2 = await query_categories(parent_id=None, offset=0, limit=10, count_only=False)
    filtered2 = [c for c in cats2 if "Upd" in c[1]]
    assert filtered2[0][1] == "Upd B" # 20
    assert filtered2[1][1] == "Upd A" # 30

@pytest.mark.asyncio
async def test_category_ordering_automatic_append(setup_test_database):
    """Test the automatic append (+10) feature directly"""
    await create_category("Auto 1", parent_id=None)
    await create_category("Auto 2", parent_id=None)

    async with Database().session() as db_session:
        result1 = await db_session.execute(select(Categories).where(Categories.name == "Auto 1"))
        cat1 = result1.scalars().first()

        result2 = await db_session.execute(select(Categories).where(Categories.name == "Auto 2"))
        cat2 = result2.scalars().first()

        assert cat1 is not None
        assert cat2 is not None

        assert cat2.display_order == cat1.display_order + 10


from bot.web.admin import normalize_parent_id

@pytest.mark.asyncio
async def test_normalize_parent_numeric_string(setup_test_database):
    async with Database().session() as s:
        c = Categories(name="Parent 1")
        s.add(c)
        await s.commit()

        # Test numeric string
        res = await normalize_parent_id(str(c.id))
        assert res == c.id

@pytest.mark.asyncio
async def test_normalize_parent_integer(setup_test_database):
    async with Database().session() as s:
        c = Categories(name="Parent 2")
        s.add(c)
        await s.commit()

        res = await normalize_parent_id(c.id)
        assert res == c.id

@pytest.mark.asyncio
async def test_normalize_parent_orm_object(setup_test_database):
    async with Database().session() as s:
        c = Categories(name="Parent 3")
        s.add(c)
        await s.commit()

        res = await normalize_parent_id(c)
        assert res == c.id

@pytest.mark.asyncio
async def test_normalize_parent_blank(setup_test_database):
    assert await normalize_parent_id("") is None
    assert await normalize_parent_id(None) is None

@pytest.mark.asyncio
async def test_normalize_parent_invalid_values(setup_test_database):
    async with Database().session() as s:
        c = Categories(name="Parent 4")
        s.add(c)
        await s.commit()

        with pytest.raises(ValueError, match="must be an integer"):
            await normalize_parent_id("abc")

        with pytest.raises(ValueError, match="cannot be negative"):
            await normalize_parent_id("-1")

        with pytest.raises(ValueError, match="does not exist"):
            await normalize_parent_id(999999)

        with pytest.raises(ValueError, match="Cannot assign a category as its own parent"):
            await normalize_parent_id(c.id, current_model_id=c.id)

@pytest.mark.asyncio
async def test_normalize_parent_cycle(setup_test_database):
    async with Database().session() as s:
        c1 = Categories(name="Cycle 1")
        c2 = Categories(name="Cycle 2")
        s.add_all([c1, c2])
        await s.commit()

        c2.parent_id = c1.id
        await s.commit()

        # Now try to set c1's parent to c2
        with pytest.raises(ValueError, match="Circular dependency"):
            await normalize_parent_id(c2.id, current_model_id=c1.id)
