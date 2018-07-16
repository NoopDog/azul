"""empty message

Revision ID: c659370cdf42
Revises: None
Create Date: 2016-11-10 16:54:26.039687

"""

# revision identifiers, used by Alembic.
revision = 'c659370cdf42'
down_revision = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.create_table('billing',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('cost', sa.Numeric(), nullable=True),
    sa.Column('project', sa.Text(), nullable=True),
    sa.Column('start_date', sa.DateTime(), nullable=True),
    sa.Column('end_date', sa.DateTime(), nullable=True),
    sa.Column('created_date', sa.DateTime(), nullable=True),
    sa.Column('closed_out', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project', 'start_date', name='unique_prj_start')
    )
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('billing')
    ### end Alembic commands ###