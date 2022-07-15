"""Add indexes to commits

Revision ID: 2
Revises: 1
Create Date: 2022-07-08 11:21:33.259034

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2'
down_revision = '1'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###

    # change index to hash type
    op.drop_index(op.f('author_affiliation'), table_name='commits', schema='augur_data')
    op.create_index('author_affiliation', 'commits', ['cmt_author_affiliation'], unique=False, schema='augur_data', postgresql_using='hash')

    # change index to hash type
    op.drop_index(op.f('committer_affiliation'), table_name='commits', schema='augur_data')
    op.create_index('committer_affiliation', 'commits', ['cmt_committer_affiliation'], unique=False, schema='augur_data', postgresql_using='hash')

    op.create_index('cmt-author-date-idx2', 'commits', ['cmt_author_date'], unique=False, schema='augur_data')
    op.create_index('cmt_author_contrib_worker', 'commits', ['cmt_author_name', 'cmt_author_email', 'cmt_author_date'], unique=False, schema='augur_data', postgresql_using='brin')
    op.create_index('cmt_commiter_contrib_worker', 'commits', ['cmt_committer_name', 'cmt_committer_email', 'cmt_committer_date'], unique=False, schema='augur_data', postgresql_using='brin')
    op.create_index('commits_idx_repo_id_cmt_ema_cmt_dat_cmt_nam', 'commits', ['repo_id', 'cmt_author_email', 'cmt_author_date', 'cmt_author_name'], unique=False, schema='augur_data')
    op.create_index('commits_idx_repo_id_cmt_ema_cmt_dat_cmt_nam2', 'commits', ['repo_id', 'cmt_committer_email', 'cmt_committer_date', 'cmt_committer_name'], unique=False, schema='augur_data')
    op.create_index('committer_email,committer_affiliation,committer_date', 'commits', ['cmt_committer_email', 'cmt_committer_affiliation', 'cmt_committer_date'], unique=False, schema='augur_data')
    op.create_index(op.f('ix_augur_data_commits_cmt_committer_raw_email'), 'commits', ['cmt_committer_raw_email'], unique=False, schema='augur_data')
    # ### end Alembic commands ###

def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_augur_data_commits_cmt_committer_raw_email'), table_name='commits', schema='augur_data')
    op.drop_index('committer_email,committer_affiliation,committer_date', table_name='commits', schema='augur_data')
    op.drop_index('commits_idx_repo_id_cmt_ema_cmt_dat_cmt_nam2', table_name='commits', schema='augur_data')
    op.drop_index('commits_idx_repo_id_cmt_ema_cmt_dat_cmt_nam', table_name='commits', schema='augur_data')
    op.drop_index('cmt_commiter_contrib_worker', table_name='commits', schema='augur_data', postgresql_using='brin')
    op.drop_index('cmt_author_contrib_worker', table_name='commits', schema='augur_data', postgresql_using='brin')
    op.drop_index('cmt-author-date-idx2', table_name='commits', schema='augur_data')

    op.drop_index(op.f('author_affiliation'), table_name='commits', schema='augur_data', postgresql_using='hash')
    op.create_index('author_affiliation', 'commits', ['cmt_author_affiliation'], unique=False, schema='augur_data')

    op.drop_index(op.f('committer_affiliation'), table_name='commits', schema='augur_data', postgresql_using='hash')
    op.create_index('committer_affiliation', 'commits', ['cmt_committer_affiliation'], unique=False, schema='augur_data')
 
    # ### end Alembic commands ###